#!/usr/bin/env python3
"""ScienceDaily -> illustrated 85s reel -> Telegram approval -> Instagram.

Local use:
    python run.py build                 # pick a story and build a reel
    python run.py build --story URL     # build one specific article
    python run.py list                  # what's waiting
    python run.py approve <slug>        # publish it now
    python run.py reject <slug>         # discard it

Called by CI:
    python run.py latest-slug           # slug of the newest pending reel
    python run.py set-video-url <slug> <url>
    python run.py notify <slug>         # send to Telegram with buttons
    python run.py poll                  # act on button presses
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import config
from core import captions, feed, publish, render, sources, visuals
from core import script as script_mod
from core.visuals import Shot

PENDING = config.STATE_DIR / "pending"
PENDING.mkdir(parents=True, exist_ok=True)

# Distinct from a crash: discovery ran fine and found nothing to build.
NO_STORY = 2


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    # Strip AGAIN after truncating. Cutting at 44 characters can land on a
    # hyphen, and a slug ending in "-" makes an invalid GitHub release tag:
    # the upload fails with HTTP 422 "Published releases must have a valid
    # tag", the reel never gets a URL for Instagram to fetch, and the
    # rendered mp4 dies with the runner. Two reels were lost to this.
    s = s[:44].strip("-") or "reel"
    return f"{datetime.now().strftime('%Y%m%d')}-{s}"


def _manifest_path(slug: str) -> Path:
    return PENDING / f"{slug}.json"


def _save(slug: str, data: dict) -> None:
    _manifest_path(slug).write_text(json.dumps(data, indent=2))


def _load(slug: str) -> dict:
    return json.loads(_manifest_path(slug).read_text())


# --------------------------------------------------------------------- build

def _build_one(args) -> int:
    print("\n[1/7] selecting story")
    if args.story:
        req = urllib.request.Request(args.story, headers={"User-Agent": feed.UA})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf8", "ignore")
        title = feed._clean(re.search(r"<title>(.*?)</title>", html, re.S).group(1))
        m = re.search(r'<meta name="description" content="(.*?)"', html, re.S)
        story = feed.Story(hashlib.sha1(args.story.encode()).hexdigest()[:16],
                           title, feed._clean(m.group(1)) if m else "",
                           args.story, "", "manual")
    else:
        already = set()
        for mf in PENDING.glob("*.json"):
            try:
                already.add(json.loads(mf.read_text()).get("story_uid"))
            except (json.JSONDecodeError, OSError):
                pass
        story = sources.pick_story(max_tier=args.max_tier,
                                   use_search=not args.no_search,
                                   exclude_uids=already)

    if not story:
        print("  no suitable story found -- nothing to do")
        return NO_STORY
    print(f"  {story.title}\n  {story.link}")

    slug = _slug(story.title)
    work = config.OUT_DIR / slug
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    article = getattr(story, "article", "") or ""
    if config.USE_FULL_ARTICLE and not article:
        print("\n[2/7] fetching article for source material")
        article = sources.fetch_article(story.link)
    print(f"\n[2/7] source material: {len(article.split())} words")

    tier = getattr(story, "tier", "safe")
    if tier != "safe":
        print(f"  ! this story is tier '{tier}' -- approval is required and "
              f"extra caveats are enforced")

    print("\n[3/7] writing script and shot list")
    guide_key = ("oddcrime" if getattr(story, "topic", "") == "oddcrime"
             else tier)
    sc = script_mod.generate(story.title, story.summary, article,
                             tier=guide_key)

    if args.hook:
        print(f"  using your hook instead of the generated one")
        sc.narration = sc.narration.replace(sc.hook, "", 1).strip()
        sc.hook = args.hook.strip()
        sc.narration = f"{sc.hook} {sc.narration}".strip()

    if not sc.shots and not args.allow_fallback:
        print("\n  ! The script provider failed, so this would be the plain "
              "fallback text with one generic image -- not worth posting.\n"
              "    Fix the provider above, or pass --allow-fallback to build "
              "it anyway.")
        shutil.rmtree(work, ignore_errors=True)
        return 1
    kcfg = sources.load_keywords()
    caption = script_mod.build_caption(
        sc, story.title, story.link,
        topic=getattr(story, 'topic', ''), cfg=kcfg)
    (work / "script.txt").write_text(sc.outline() + "\n")
    print(f"  {len(sc.shots)} shots planned")
    print(f"\n  HOOK: {sc.hook}")
    for i, alt in enumerate(sc.hook_options, 1):
        if alt.strip() and alt.strip() != sc.hook.strip():
            print(f"    alt {i}: {alt[:100]}")

    print("\n[4/7] generating narration")
    narration = sc.narration
    if config.CTA_TEXT.strip():
        narration = f"{narration} {config.CTA_TEXT.strip()}"
    audio = work / "narration.mp3"
    words = _speak(narration, audio)
    total = words[-1].end if words else 0.0
    engine = ""
    try:
        from core import tts as _tts
        engine = _tts.last_engine
    except Exception:
        pass
    print(f"  {total:.1f}s, {len(words)} words"
          + (f" [{engine} voice]" if engine else ""))
    # A fallback engine sounds noticeably worse. Say so once, loudly,
    # rather than quietly shipping a robotic reel and leaving you to
    # wonder.
    #
    # Both the test and the message used to be hardcoded to "edge",
    # which was right while TTS_PROVIDERS was pinned to "edge,gtts".
    # Once the chain became "gemini,..." this fired on EVERY build and
    # blamed edge for an outage that had not happened -- console line
    # and Telegram alert alike. The one warning that would have caught
    # the 21 Oct fall-through to gtts was already crying wolf, which is
    # why four days of robotic reels went out unnoticed.
    #
    # Two separate things now. Any engine that is not the chain leader
    # is worth a console line. Only a genuinely last-resort engine --
    # the two that actually sound robotic -- is worth interrupting you.
    _lead = next((n.strip() for n in config.TTS_PROVIDERS.split(",")
                  if n.strip()), "")
    if engine and _lead and engine != _lead:
        print(f"  ! this reel uses the '{engine}' voice, not '{_lead}' -- "
              f"'{_lead}' was unavailable and quality is lower than usual")
    if engine in ("gtts", "espeak"):
        _why = ("gtts is female and gets pitched down to sound male"
                if engine == "gtts" else
                "espeak is formant synthesis, not a neural voice")
        print(f"  ! '{engine}' is a last-resort engine -- {_why}, so this "
              f"reel sounds robotic. Everything above it in "
              f"'{config.TTS_PROVIDERS}' refused.")
        try:
            from core import telegram
            if telegram.enabled():
                telegram.send_text(
                    f"Narration fell all the way to '{engine}' -- {_why}, "
                    f"so this one sounds robotic. Everything above it in "
                    f"'{config.TTS_PROVIDERS}' refused. It still posts. "
                    f"Reel: {story.title[:70]}")
        except Exception:
            pass

    print("\n[5/7] sourcing imagery")
    shots = [Shot(query=s.get("query", ""), cue=s.get("cue", ""))
             for s in sc.shots if s.get("query")]
    if not shots:
        shots = [Shot(query=" ".join(story.title.split()[:4]), cue="")]
    shots = visuals.align_shots(shots, words, total)

    before = len(shots)
    shots = visuals.split_long_shots(shots)
    avg = total / max(1, len(shots))
    print(f"  {before} beats -> {len(shots)} shots "
          f"(~{avg:.1f}s each)")

    used: set[str] = set()
    found = sum(visuals.resolve_shot(s, work, i, used)
                for i, s in enumerate(shots))
    print(f"  {found}/{len(shots)} images found")

    caption = script_mod.build_caption(
        sc, story.title, story.link, topic=getattr(story, "topic", ""),
        cfg=kcfg, credits=visuals.credits_block(shots))
    (work / "caption.txt").write_text(caption)
    print(f"  caption: {len(caption)} chars")

    print("\n[6/7] rendering")
    captions.build_srt(words, work / "captions.srt")
    ass = captions.build_ass(words, work / "captions.ass")
    sequence = render.build_sequence(shots, work)
    music = render.pick_music(getattr(story, "topic", ""))
    print(f"  music: {music.name}" if music
          else "  music: none (drop files in assets/music/)")
    reel = render.finish(sequence, audio, ass, work / "reel.mp4",
                         kicker=args.kicker, music=music, workdir=work,
                         cover=getattr(sc, "cover_text", ""))
    dur = render.probe_duration(reel)
    print(f"  {reel} ({reel.stat().st_size/1e6:.1f} MB, {dur:.1f}s)")

    _save(slug, {
        "slug": slug,
        "title": story.title,
        "link": story.link,
        "story_uid": story.uid,
        "caption": caption,
        "duration": dur,
        "shots_found": f"{found}/{len(shots)}",
        # Which voice actually spoke. Printed to the log before, and
        # only there -- so when the chain quietly fell through to gtts
        # on 21 Oct there was nothing left afterwards to show it, and it
        # took four days and a spectrum analysis of the published mp4s
        # to find. One field here makes it greppable for ever:
        #   grep -h tts_engine state/pending/*.json | sort | uniq -c
        "tts_engine": engine,
        "cover_text": getattr(sc, "cover_text", ""),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "tier": tier,
        "video_url": "",
    })

    print("\n[7/7] awaiting approval")
    if args.auto and tier != "safe":
        print(f"  ! refusing --auto for a '{tier}' story. Breaking news is "
              f"wrong often enough in the first hours that this one needs "
              f"your eyes. Approve it manually.")
        return 0
    if args.auto:
        return _publish(slug)
    print(f"  python run.py approve {slug}")
    return 0


def cmd_build(args) -> int:
    """Build one reel, or several in a row.

    Each pass marks its story seen only on publish, but the pending
    manifest keeps it out of the next pick, so a run of N gives N
    different stories.
    """
    count = max(1, int(getattr(args, "count", 1) or 1))
    made = 0
    for i in range(count):
        if count > 1:
            print(f"\n{'='*58}\n  REEL {i + 1} of {count}\n{'='*58}")
        rc = _build_one(args)
        if rc == NO_STORY:
            # The pool does not refill mid-run, so a second pass would ask
            # the same question and get the same answer.
            print("  ! no usable story -- not retrying the same pool")
            break
        if rc != 0:
            print(f"  stopping after {made} reel(s)")
            break
        made += 1
    print(f"\n  built {made} reel(s)")
    if not made:
        print("  ! nothing was built, so this exits non-zero on purpose -- "
              "a run that produces no reel must not look successful")
    return 0 if made else 1


def _speak(text: str, path: Path):
    try:
        from core import tts
    except ImportError:
        print("  ! edge-tts missing: pip install edge-tts")
        raise
    return tts.speak(text, path)


# ------------------------------------------------------------------ publish

def _publish(slug: str) -> int:
    man = _load(slug)
    if man.get("media_id"):
        print(f"  {slug} already published as {man['media_id']} -- refusing "
              f"to post it twice")
        if man.get("status") != "published":
            man["status"] = "published"
            _save(slug, man)
        return 0
    url = man.get("video_url") or ""
    if not url:
        reel = config.OUT_DIR / slug / "reel.mp4"
        if not reel.exists():
            print(f"  ! no video_url and no local file for {slug}")
            return 1
        url = publish.host(reel)
        man["video_url"] = url
        _save(slug, man)

    media_id = publish.publish_reel(url, man["caption"])
    man.update(status="published", media_id=media_id,
               published_at=datetime.now(timezone.utc).isoformat())
    _save(slug, man)
    feed.mark_seen(man["story_uid"])
    print(f"  published {media_id}")
    return 0


def cmd_approve(args) -> int:
    if not _manifest_path(args.slug).exists():
        print(f"no such reel: {args.slug}")
        return 1
    return _publish(args.slug)


def cmd_reject(args) -> int:
    if not _manifest_path(args.slug).exists():
        print(f"no such reel: {args.slug}")
        return 1
    man = _load(args.slug)
    feed.mark_seen(man["story_uid"])
    man["status"] = "rejected"
    _save(args.slug, man)
    print(f"rejected {args.slug}")
    return 0


# ----------------------------------------------------------------- CI hooks

def cmd_latest_slug(args) -> int:
    pend = [json.loads(p.read_text()) for p in PENDING.glob("*.json")]
    pend = [m for m in pend if m.get("status") == "pending"]
    if not pend:
        return 0
    print(max(pend, key=lambda m: m["built_at"])["slug"])
    return 0


def cmd_pending_slugs(args) -> int:
    rows = []
    for mf in PENDING.glob("*.json"):
        try:
            m = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if m.get("status") != "pending" or m.get("video_url"):
            continue
        # Only list reels whose video actually exists here. A manifest can
        # outlive its file (built on another machine, out/ is gitignored).
        if not (config.OUT_DIR / m["slug"] / "reel.mp4").exists():
            continue
        rows.append((m["built_at"], m["slug"]))
    for _, slug in sorted(rows):
        print(slug)
    return 0


def cmd_backfill_slugs(args) -> int:
    """The oldest published reels that never reached YouTube.

    One slug per line, so the workflow can fetch each one's release
    asset back into out/ -- which is the only thing standing between
    cmd_youtube and uploading them.
    """
    rows = []
    for mf in PENDING.glob("*.json"):
        try:
            man = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if man.get("status") != "published" or man.get("youtube_id"):
            continue
        rows.append((man.get("built_at", ""), man["slug"]))
    rows.sort()
    try:
        limit = max(1, int(getattr(args, "count", 8) or 8))
    except (TypeError, ValueError):
        limit = 8
    for _, slug in rows[:limit]:
        print(slug)
    return 0


def cmd_hours_since_build(args) -> int:
    """Hours since the newest reel was built, or 999 if there are none.

    Lets a catch-up run decide whether it is needed, so an extra cron slot
    is insurance against a dropped schedule rather than extra volume.
    """
    newest = None
    for mf in PENDING.glob("*.json"):
        try:
            ts = json.loads(mf.read_text()).get("built_at")
            when = datetime.fromisoformat(ts) if ts else None
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            continue
        if when and (newest is None or when > newest):
            newest = when
    if newest is None:
        print("999")
        return 0
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - newest).total_seconds() / 3600
    print(f"{hours:.1f}")
    return 0


def cmd_doctor(args) -> int:
    """One report on whether the pipeline can actually do its job.

    Written after a morning spent asking five commands what one should
    have answered. Checks credentials, the account, the story pool and
    state, and says plainly what is broken.
    """
    problems: list[str] = []

    print("credentials")
    for name, val, needed_for in (
        ("GEMINI_API_KEY", config.GEMINI_API_KEY, "writing scripts"),
        ("IG_USER_ID", config.IG_USER_ID, "posting and dedupe checks"),
        ("IG_ACCESS_TOKEN", config.IG_ACCESS_TOKEN, "posting"),
        ("TELEGRAM_BOT_TOKEN", config.TELEGRAM_BOT_TOKEN, "alerts"),
        ("TELEGRAM_CHAT_ID", config.TELEGRAM_CHAT_ID, "alerts"),
    ):
        if val:
            print(f"  ok      {name} ({len(val)} chars)")
        else:
            print(f"  MISSING {name} -- needed for {needed_for}")
            problems.append(f"{name} not set")

    print("\naccount")
    if config.IG_USER_ID and config.IG_ACCESS_TOKEN:
        try:
            me = publish._graph_get(config.IG_USER_ID, {
                "fields": "username,media_count",
                "access_token": config.IG_ACCESS_TOKEN})
            print(f"  ok      @{me.get('username')} "
                  f"({me.get('media_count')} posts)")
        except Exception as exc:
            print(f"  FAILED  {str(exc)[:200]}")
            problems.append("Instagram token rejected")
        quota = publish.remaining_quota()
        print(f"  quota   {quota if quota is not None else 'unknown'} "
              f"posts left in this 24h window")
    else:
        print("  skipped (no credentials in this shell)")

    print("\nsettings")
    print(f"  auto-publish {'ON' if config.AUTO_PUBLISH else 'OFF'}, "
          f"window {config.AUTO_PUBLISH_MINUTES} min, "
          f"max tier {config.AUTO_PUBLISH_MAX_TIER}")

    print("\nstate")
    pend = pub = rej = 0
    for mf in sorted(PENDING.glob("*.json")):
        try:
            man = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        status = man.get("status")
        if status == "published":
            pub += 1
            continue
        if status == "rejected":
            rej += 1
            continue
        pend += 1
        print(f"  pending {man['slug']}")
        print(f"          {_why_pending(man)}")
        if config.IG_USER_ID and config.IG_ACCESS_TOKEN:
            try:
                live = publish.already_posted(man.get("caption", ""))
                if live:
                    print(f"          BUT it is live as {live} -- "
                          f"run: python run.py reconcile")
                    problems.append(f"stale state for {man['slug']}")
            except publish.LookupUnavailable as exc:
                print(f"          could not check the account: "
                      f"{str(exc)[:120]}")
    print(f"  {pub} published, {pend} pending, {rej} rejected")

    print("\nstory pool")
    try:
        cfg = sources.load_keywords()
        # Enablement lives in enabled_topics, not a per-topic flag -- the
        # obvious guess reports every topic as on.
        all_topics = cfg.get("topics", {})
        enabled = cfg.get("enabled_topics") or list(all_topics)
        feeds = {u for t, v in all_topics.items() if t in enabled
                 for u in v.get("feeds", [])}
        print(f"  ok      {len(enabled)}/{len(all_topics)} topics enabled, "
              f"{len(feeds)} feeds in use")
        print(f"          feed health is slow to test: "
              f"python run.py keywords check-feeds")
    except Exception as exc:
        print(f"  FAILED  could not read keywords.json: {exc}")
        problems.append("keywords.json unreadable")

    print("\n" + ("-" * 58))
    if problems:
        print("problems found:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("no problems found")
    return 0


def cmd_reconcile(args) -> int:
    """Match local state against the account, without posting anything.

    After a lost state commit the repo can disagree with reality. This
    reads the account and corrects the manifests; it never publishes, so
    it is safe to run when some pending reels are ones you would rather
    not post at all.
    """
    fixed = still = unknown = 0
    for mf in sorted(PENDING.glob("*.json")):
        try:
            man = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if man.get("status") != "pending":
            continue
        existing = man.get("media_id")
        if not existing:
            try:
                existing = publish.already_posted(man.get("caption", ""))
            except publish.LookupUnavailable as exc:
                # Reporting "not posted" here would be a guess dressed up
                # as a fact, and acting on it could duplicate a post.
                print(f"  CANNOT TELL: {man['slug']}\n      {exc}")
                unknown += 1
                continue
        if existing:
            man.update(status="published", media_id=existing)
            _save(man["slug"], man)
            feed.mark_seen(man["story_uid"])
            print(f"  live as {existing}, marked published: {man['slug']}")
            fixed += 1
        else:
            print(f"  genuinely not posted: {man['slug']}")
            still += 1
    print(f"\n  corrected {fixed}, still unposted {still}, "
          f"could not check {unknown}")
    return 1 if unknown else 0


def _cover_frame(video: Path) -> Path | None:
    """Pull one frame from inside the cover hold, so the catchphrase is on
    it. Returns None if ffmpeg cannot, which is not worth failing over."""
    import subprocess

    at = max(0.05, config.COVER_THUMB_MS / 1000.0)
    if at >= config.COVER_SECONDS:
        # Past the hold the overlay has faded and the frame would be bare.
        at = max(0.05, config.COVER_SECONDS * 0.4)
    out = video.with_name("thumbnail.jpg")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}",
             "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out)],
            check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out if out.exists() and out.stat().st_size > 2000 else None


def cmd_youtube(args) -> int:
    """Upload published reels to YouTube as Shorts.

    Only reels that actually went out on Instagram, and only once each --
    the youtube_id in the manifest is the guard, the same shape that
    stopped the duplicate Instagram posts.
    """
    from core import youtube

    if not youtube.enabled():
        print("  youtube not configured, skipping")
        return 0

    only = getattr(args, "slug", "") or ""
    done = failed = skipped = 0

    for mf in sorted(PENDING.glob("*.json")):
        try:
            man = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if only and man.get("slug") != only:
            continue
        if man.get("status") != "published":
            continue
        if man.get("youtube_id"):
            skipped += 1
            continue

        reel = config.OUT_DIR / man["slug"] / "reel.mp4"
        if not reel.exists():
            # Built on another runner; the mp4 is gone with it.
            print(f"  skip (no local file): {man['slug']}")
            skipped += 1
            continue

        title = youtube.build_title(man.get("title", ""),
                                    man.get("cover_text", ""))
        desc = youtube.build_description(man.get("caption", ""),
                                         man.get("link", ""))
        print(f"  uploading {man['slug']}")
        print(f"    title: {title}")
        try:
            vid = youtube.upload(reel, title, desc)
        except Exception as exc:
            print(f"  ! youtube failed for {man['slug']}: {str(exc)[:200]}")
            man["youtube_error"] = str(exc)[:300]
            _save(man["slug"], man)
            failed += 1
            continue

        man["youtube_id"] = vid
        man.pop("youtube_error", None)
        print(f"    -> {youtube.watch_url(vid)}")

        # The cover frame as the grid thumbnail. Without this YouTube
        # picks its own frame, which is why Hotsciencenews showed videos
        # with no catchphrase on them. Cosmetic, and it runs after the
        # video is live, so a failure is recorded and nothing more.
        thumb = _cover_frame(reel)
        if thumb:
            why = youtube.set_thumbnail(vid, thumb)
            if why:
                man["thumbnail_note"] = why[:160]
                print(f"    thumbnail not set: {why[:110]}")
            else:
                man["thumbnail"] = "set"
                print(f"    thumbnail: the cover frame at "
                      f"{config.COVER_THUMB_MS}ms")
        _save(man["slug"], man)
        done += 1

    print(f"  youtube: {done} uploaded, {skipped} skipped, {failed} failed")
    # Never fail the run over YouTube -- Instagram has already posted.
    return 0


def cmd_alert(args) -> int:
    """Send a plain Telegram message. Used to report a run that built
    nothing, so a dead pipeline is noticed without reading Actions logs."""
    from core import telegram
    if not telegram.enabled():
        print("  telegram not configured, skipping alert")
        return 0
    telegram.send_text(args.text)
    print("  alert sent")
    return 0


def cmd_set_video_url(args) -> int:
    man = _load(args.slug)
    man["video_url"] = args.url
    _save(args.slug, man)
    print(f"video_url set for {args.slug}")
    return 0


def cmd_notify(args) -> int:
    from core import telegram
    if not telegram.enabled():
        print("  telegram not configured, skipping")
        return 0
    man = _load(args.slug)
    reel = config.OUT_DIR / args.slug / "reel.mp4"
    telegram.send_for_approval(reel, args.slug, man["title"],
                               man["caption"], man["duration"])
    print(f"  sent {args.slug} for approval")
    return 0


def cmd_poll(args) -> int:
    from core import telegram
    if not telegram.enabled():
        print("  telegram not configured")
        return 0

    decisions = telegram.fetch_decisions()
    if not decisions:
        print("  no new decisions")
        return 0

    print(f"  {len(decisions)} decision(s): "
          + ", ".join(f"{a}:{s_[:28]}" for a, s_, _, _ in decisions))

    for action, slug, chat_id, msg_id in decisions:
        if not _manifest_path(slug).exists():
            print(f"  ! unknown reel: {slug}")
            telegram.mark_done(chat_id, msg_id, f"Unknown reel: {slug}")
            continue

        man = _load(slug)
        if man.get("status") != "pending":
            print(f"  i already {man.get('status')}: {slug}")
            telegram.mark_done(chat_id, msg_id,
                               f"Already {man.get('status')}: {slug}")
            continue

        if action == "no":
            print(f"  rejecting {slug}")
            cmd_reject(argparse.Namespace(slug=slug))
            telegram.mark_done(chat_id, msg_id, f"Rejected: {man['title'][:80]}")
            continue

        try:
            print(f"  approving {slug}")
            _publish(slug)
            telegram.mark_done(chat_id, msg_id,
                               f"Posted to Instagram: {man['title'][:80]}")
        except Exception as exc:
            telegram.mark_done(chat_id, msg_id, f"Publish failed: {exc}")
            return 1
    return 0



# ---------------------------------------------------------------- keywords

def cmd_keywords(args) -> int:
    cfg = sources.load_keywords()
    topics = cfg.setdefault("topics", {})

    if args.action == "list":
        enabled = cfg.get("enabled_topics", [])
        for name, spec in topics.items():
            on = "ON " if name in enabled else "off"
            tier = spec.get("tier", "safe")
            wait = sources.MIN_AGE_HOURS.get(tier, 0)
            delay = f", waits {wait}h" if wait else ""
            print(f"\n  [{on}] {name}  (tier: {tier}{delay})")
            for k in spec.get("keywords", []):
                print(f"         - {k}")
            for f in spec.get("feeds", []):
                print(f"         feed: {f}")
        print(f"\n  blocked terms: {', '.join(cfg.get('blocked_terms', []))}")
        print(f"\n  enabled topics: {', '.join(enabled) or '(none)'}")
        return 0

    if args.action in ("add", "remove"):
        if not args.topic or not args.words:
            print("need --topic and one or more keywords")
            return 1
        spec = topics.setdefault(args.topic, {"tier": "sensitive",
                                              "keywords": [], "feeds": []})
        kws = spec.setdefault("keywords", [])
        for w in args.words:
            if args.action == "add" and w not in kws:
                kws.append(w)
            elif args.action == "remove" and w in kws:
                kws.remove(w)
        sources.save_keywords(cfg)
        print(f"{args.topic}: {len(kws)} keywords")
        return 0

    if args.action in ("enable", "disable"):
        if not args.topic:
            print("need --topic")
            return 1
        enabled = cfg.setdefault("enabled_topics", [])
        if args.action == "enable" and args.topic not in enabled:
            enabled.append(args.topic)
            tier = topics.get(args.topic, {}).get("tier", "safe")
            if tier != "safe":
                print(f"  note: '{args.topic}' is tier '{tier}' -- it will "
                      f"never auto-post, and waits "
                      f"{sources.MIN_AGE_HOURS.get(tier)}h before a story "
                      f"is eligible.")
        elif args.action == "disable" and args.topic in enabled:
            enabled.remove(args.topic)
        sources.save_keywords(cfg)
        print(f"enabled: {', '.join(enabled) or '(none)'}")
        return 0

    if args.action == "check-feeds":
        # I have shipped dead feed URLs more than once by guessing. This
        # tests every one and offers to prune the failures, so the list
        # stays honest without anyone reading 404s in a build log.
        import urllib.request
        from core.feed import UA
        bad = []
        for name, spec in topics.items():
            for url in spec.get("feeds", []):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        body = r.read(4000)
                    items = body.count(b"<item") + body.count(b"<entry")
                    if items:
                        print(f"  ok   [{name:12}] {items:2d}+ items  {url}")
                    else:
                        print(f"  EMPTY[{name:12}]           {url}")
                        bad.append((name, url))
                except Exception as exc:
                    print(f"  DEAD [{name:12}] {type(exc).__name__}: "
                          f"{str(exc)[:40]}  {url}")
                    bad.append((name, url))

        if not bad:
            print("\n  all feeds healthy")
            return 0
        print(f"\n  {len(bad)} problem feed(s)")
        if args.prune:
            for name, url in bad:
                topics[name]["feeds"].remove(url)
            sources.save_keywords(cfg)
            print("  pruned from keywords.json")
        else:
            print("  re-run with --prune to remove them")
        return 0

    if args.action == "preview":
        found = sources.gather(cfg, use_search=not args.no_search)
        print(f"\n  {len(found)} stories matched:\n")
        for st in found[:20]:
            print(f"  {st.score:5.1f} [{st.topic}/{st.tier}] {st.title[:62]}")
            print(f"        {st.publisher or '?'} | matched: "
                  f"{', '.join(st.matched[:4])}")
        return 0

    return 1


def cmd_autopublish(args) -> int:
    """Publish pending reels older than the review window.

    Anything rejected is skipped (cmd_reject sets status). Tiers above
    AUTO_PUBLISH_MAX_TIER are left for an explicit decision.
    """
    from datetime import datetime, timezone
    from core.sources import TIER_ORDER

    if not config.AUTO_PUBLISH and not args.force:
        print("  auto-publish disabled")
        return 0

    wait = args.minutes if args.minutes is not None else config.AUTO_PUBLISH_MINUTES
    cap = TIER_ORDER.get(config.AUTO_PUBLISH_MAX_TIER, 1)
    now = datetime.now(timezone.utc)
    published = failed = 0

    for mf in sorted(PENDING.glob("*.json")):
        try:
            man = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if man.get("status") != "pending":
            continue

        # A manifest can say "pending" while the reel is already live: the
        # publish succeeded and the state commit that recorded it never
        # reached git. Posting again would duplicate it on the account, so
        # a media_id is treated as proof it went out, and the local status
        # is corrected instead.
        if man.get("media_id"):
            print(f"  already posted as {man['media_id']}, fixing status: "
                  f"{man['slug']}")
            man["status"] = "published"
            _save(man["slug"], man)
            continue

        tier = man.get("tier", "safe")
        if TIER_ORDER.get(tier, 0) > cap:
            print(f"  skip (tier {tier}, needs your tap): {man['slug']}")
            continue

        try:
            built = datetime.fromisoformat(man["built_at"])
        except (KeyError, ValueError):
            continue
        age_min = (now - built).total_seconds() / 60
        if age_min < wait:
            print(f"  skip ({age_min:.0f}/{wait} min old): {man['slug']}")
            continue

        if not man.get("video_url"):
            print(f"  skip (never uploaded): {man['slug']}")
            continue

        # Last line of defence against a double post: ask the account. If
        # the account cannot be reached, go ahead -- a Graph outage must
        # not stop the pipeline.
        try:
            existing = publish.already_posted(man.get("caption", ""))
        except publish.LookupUnavailable as exc:
            print(f"  (could not check for an existing post: {exc})")
            existing = None
        if existing:
            print(f"  already on the account as {existing}, not posting "
                  f"again: {man['slug']}")
            man.update(status="published", media_id=existing)
            _save(man["slug"], man)
            feed.mark_seen(man["story_uid"])
            continue

        print(f"  auto-publishing {man['slug']} ({age_min:.0f} min old)")
        try:
            _publish(man["slug"])
            published += 1
        except Exception as exc:
            print(f"  ! failed: {exc}")
            failed += 1
            # Persist the reason. Without this a reel sits at "pending"
            # with no record of why, and the only way to find out is to
            # dig through a runner log that may have expired.
            try:
                man = _load(man["slug"])
                man["last_error"] = str(exc)[:400]
                man["last_attempt_at"] = now.isoformat()
                man["attempts"] = int(man.get("attempts", 0)) + 1
                _save(man["slug"], man)
            except Exception:
                pass
            try:
                from core import telegram
                if telegram.enabled():
                    telegram.send_text(f"Auto-publish failed for "
                                       f"{man['title'][:70]}\n{exc}")
            except Exception:
                pass

    print(f"  auto-published {published}, {failed} failed")
    # Returning 0 with failures on the board is how a broken publish looked
    # like a healthy run. Each failure has already been alerted and written
    # into its manifest; this makes the step red as well.
    return 1 if failed else 0


def _why_pending(man: dict) -> str:
    """One phrase explaining what a pending reel is waiting for."""
    from core.sources import TIER_ORDER
    if man.get("media_id"):
        return "already has a media_id -- state is stale, run reconcile"
    if err := man.get("last_error"):
        return f"last attempt failed ({man.get('attempts', 1)}x): {err[:90]}"
    if not man.get("video_url"):
        return "never uploaded, so there is no URL for Instagram to fetch"
    tier = man.get("tier", "safe")
    if TIER_ORDER.get(tier, 0) > TIER_ORDER.get(config.AUTO_PUBLISH_MAX_TIER, 1):
        return f"tier '{tier}' needs an explicit approval"
    try:
        built = datetime.fromisoformat(man["built_at"])
        age = (datetime.now(timezone.utc) - built).total_seconds() / 60
    except (KeyError, ValueError):
        return "no usable built_at timestamp"
    if age < config.AUTO_PUBLISH_MINUTES:
        return (f"inside the review window "
                f"({age:.0f}/{config.AUTO_PUBLISH_MINUTES} min)")
    if not config.AUTO_PUBLISH:
        return "ripe, but AUTO_PUBLISH is off"
    return f"ripe ({age:.0f} min old) -- should publish on the next run"


def cmd_list(args) -> int:
    rows = sorted(PENDING.glob("*.json"))
    if not rows:
        print("nothing built yet")
        return 0
    stale = False
    for p in rows:
        m = json.loads(p.read_text())
        print(f"  [{m['status']:9}] {m['slug']}\n              {m['title'][:60]}")
        if m.get("status") == "pending":
            print(f"              -> {_why_pending(m)}")
            stale = True
    # This listing is the repo's record, not the account's. They disagreed
    # all morning and nothing said so.
    if stale and not (config.IG_USER_ID and config.IG_ACCESS_TOKEN):
        print("\n  note: this is what the repo records, which can lag behind\n"
              "  what is actually on Instagram. No IG credentials in this\n"
              "  shell, so it could not be checked -- run reconcile on the\n"
              "  runner (gh workflow run \"Poll approvals\") to settle it.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--story"); b.add_argument("--auto", action="store_true")
    b.add_argument("--kicker")
    b.add_argument("--allow-fallback", action="store_true",
                   help="build even if the script provider failed")
    b.add_argument("--hook",
                   help="write the opening line yourself, overriding the model")
    b.add_argument("--max-tier", default="safe",
                   choices=["safe", "caution", "sensitive"],
                   help="highest risk tier to consider (default: safe)")
    b.add_argument("--no-search", action="store_true",
                   help="use only the listed RSS feeds, skip Google News")
    b.add_argument("--count", type=int, default=1,
                   help="how many reels to build in this run")
    b.set_defaults(fn=cmd_build)

    for name, fn in (("approve", cmd_approve), ("reject", cmd_reject),
                     ("notify", cmd_notify)):
        s = sub.add_parser(name); s.add_argument("slug"); s.set_defaults(fn=fn)

    s = sub.add_parser("set-video-url")
    s.add_argument("slug"); s.add_argument("url"); s.set_defaults(fn=cmd_set_video_url)

    a = sub.add_parser("autopublish")
    a.add_argument("--minutes", type=int,
                   help="override the review window")
    a.add_argument("--force", action="store_true",
                   help="run even if AUTO_PUBLISH is off")
    a.set_defaults(fn=cmd_autopublish)

    for name, fn in (("list", cmd_list), ("poll", cmd_poll),
                     ("latest-slug", cmd_latest_slug),
                     ("pending-slugs", cmd_pending_slugs),
                     ("hours-since-build", cmd_hours_since_build),
                     ("reconcile", cmd_reconcile),
                     ("doctor", cmd_doctor)):
        s = sub.add_parser(name); s.set_defaults(fn=fn)

    s = sub.add_parser("alert")
    s.add_argument("text"); s.set_defaults(fn=cmd_alert)

    s = sub.add_parser("backfill-slugs")
    s.add_argument("count", nargs="?", type=int, default=8)
    s.set_defaults(fn=cmd_backfill_slugs)

    y = sub.add_parser("youtube")
    y.add_argument("slug", nargs="?", default="")
    y.set_defaults(fn=cmd_youtube)

    k = sub.add_parser("keywords")
    k.add_argument("action",
                   choices=["list", "add", "remove", "enable", "disable",
                            "preview", "check-feeds"])
    k.add_argument("words", nargs="*")
    k.add_argument("--topic")
    k.add_argument("--no-search", action="store_true")
    k.add_argument("--prune", action="store_true",
                   help="with check-feeds: delete dead feeds")
    k.set_defaults(fn=cmd_keywords)

    args = p.parse_args()
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
