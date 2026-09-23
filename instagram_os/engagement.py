"""Comment monitor + response agent for the account's OWN media.

new comment -> parent post context -> brand context -> Claude classifies + drafts
-> thresholds, category rules, autonomy, caps, loop guards -> reply | suggest | escalate
"""
from datetime import timedelta

from .client import InstagramAPIError
from .models import CommentDecision as D
from .reasoner import brand_context, reply_guardrail_issues
from .store import iso, utcnow

DRY = ":dry-run"


class CommentMonitor:
    def __init__(self, ctx, store, client, activity, reasoner, max_comments_per_run=50):
        self.ctx, self.store, self.client, self.log, self.reasoner = ctx, store, client, activity, reasoner
        self.cfg = ctx.config["engagement"]
        self.max_comments = max_comments_per_run
        self._me = None

    @property
    def me(self):
        if self._me is None:
            acct = self.client.get_account()
            self._me = {"id": str(acct.get("id") or self.client.account_id), "username": (acct.get("username") or "").lower()}
        return self._me

    def _is_own(self, c):
        frm = c.get("from") or {}
        return str(frm.get("id")) == self.me["id"] or (c.get("username") or "").lower() == self.me["username"]

    # ------------------------------------------------------------------ run
    def run(self):
        mode = self.ctx.autonomy("reply_to_comment")
        if mode == "off" or not self.cfg.get("enabled", True):
            return {"skipped": "engagement disabled"}
        context = brand_context(self.ctx)
        counts = {d.value: 0 for d in D}
        processed = 0
        for media in self.client.list_media(limit=self.cfg["media_lookback"]):
            if media.get("comments_count") == 0:
                continue
            try:
                comments = self.client.list_comments(media["id"])
            except InstagramAPIError as e:
                self.log.record("ENGAGEMENT", f"Could not read comments on {media['id']}", error=str(e),
                                api_action="GET /{media-id}/comments")
                continue
            for top in comments:
                thread = [top] + list((top.get("replies") or {}).get("data", []))
                for c in thread:
                    if processed >= self.max_comments:
                        break
                    decision = self._handle(c, top, thread, media, context, mode)
                    if decision:
                        counts[decision.value] += 1
                        processed += 1
        self.log.record("ENGAGEMENT", f"Comment scan: {processed} new comment(s)",
                        result=", ".join(f"{k}={v}" for k, v in counts.items() if v) or "nothing new")
        return counts

    def _already_handled(self, cid):
        row = self.store.get_comment(cid)
        if row is None:
            return False
        # Dry-run decisions are re-evaluated once the brand goes live.
        return not (row["reasoner"] or "").endswith(DRY) or self.client.dry_run

    def _handle(self, c, top, thread, media, context, mode):
        cid = str(c["id"])
        if self._is_own(c) or self._already_handled(cid):
            return None
        username = c.get("username") or (c.get("from") or {}).get("username") or ""
        base = dict(media_id=str(media["id"]), parent_id=c.get("parent_id") or (None if c is top else str(top["id"])),
                    username=username, user_id=str((c.get("from") or {}).get("id") or ""), text=c.get("text"),
                    timestamp=c.get("timestamp"), reasoner=self.reasoner.name + (DRY if self.client.dry_run else ""))
        self.log.record("ENGAGEMENT", f"New comment detected from @{username} on {media['id']}", ref=cid)

        own_in_thread = sum(1 for r in thread if self._is_own(r))
        if c is top and own_in_thread:
            return self._decide(cid, base, D.IGNORED, "already answered from the account")

        thread_text = "\n".join(f"@{r.get('username')}: {r.get('text')}" for r in thread if r is not c)
        try:
            a = self.reasoner.classify_comment(c, media.get("caption"), thread_text, context,
                                               max_chars=self.cfg["reply_max_chars"])
        except Exception as e:  # any reasoner failure (ReasonerError, API, network) escalates, never auto-replies
            return self._decide(cid, base, D.ESCALATED, f"reasoner error: {type(e).__name__}")
        a.confidence = max(0.0, min(1.0, float(a.confidence)))
        base.update(category=a.category, confidence=a.confidence, rationale=a.rationale)
        self.log.record("CLASSIFICATION", f"{a.category.replace('_', ' ').title()} / confidence {a.confidence:.2f}",
                        reason=a.rationale, tool=f"reasoner:{self.reasoner.name}", confidence=a.confidence, ref=cid)

        t = self.cfg["thresholds"]
        if a.category in self.cfg["no_reply_categories"]:
            if a.category == "spam":
                self._maybe_hide(cid, a.confidence)
            return self._decide(cid, base, D.IGNORED, f"{a.category}: no reply by policy")
        if not a.should_reply or not a.reply.strip():
            if a.category in self.cfg["always_human_categories"] or a.confidence < t["suggest"] or a.risk_flags:
                return self._decide(cid, base, D.ESCALATED, "no safe reply drafted: " + ("; ".join(a.risk_flags) or a.category))
            return self._decide(cid, base, D.IGNORED, "no reply needed")
        if a.confidence < t["suggest"]:
            return self._decide(cid, base, D.ESCALATED, f"confidence {a.confidence:.2f} < {t['suggest']}")

        base["suggested_reply"] = a.reply.strip()
        blockers = self._auto_blockers(a, c, username, own_in_thread, mode)
        if blockers:
            return self._decide(cid, base, D.PENDING_APPROVAL, "; ".join(blockers))
        return self._send(cid, base, a.reply.strip(), auto=True)

    def _auto_blockers(self, a, c, username, own_in_thread, mode):
        t = self.cfg["thresholds"]
        out = []
        if mode != "auto":
            out.append(f"autonomy.reply_to_comment={mode}")
        if a.confidence < t["auto_reply"]:
            out.append(f"confidence {a.confidence:.2f} < auto threshold {t['auto_reply']}")
        if a.category in self.cfg["always_human_categories"]:
            out.append(f"category '{a.category}' always needs a human")
        if a.risk_flags:
            out.append("risk flags: " + ", ".join(a.risk_flags))
        out.extend(reply_guardrail_issues(a.reply, username, self.cfg["reply_max_chars"]))
        if own_in_thread >= self.cfg["max_own_replies_per_thread"]:
            out.append("loop guard: already replied in this thread")
        now = utcnow()
        if self.store.replies_since(now - timedelta(hours=1)) >= self.cfg["max_replies_per_hour"]:
            out.append("hourly reply cap reached")
        if self.store.replies_since(now - timedelta(days=1)) >= self.cfg["max_replies_per_day"]:
            out.append("daily reply cap reached")
        if username and self.store.replies_since(now - timedelta(days=1), username) >= self.cfg["max_replies_per_user_per_day"]:
            out.append(f"per-user daily cap reached for @{username}")
        same_text = self.store.conn.execute(
            "SELECT COUNT(*) FROM comments WHERE reply_text=? AND replied_at >= ?",
            (a.reply.strip(), iso(now - timedelta(days=1)))).fetchone()[0]
        if same_text >= 2:
            out.append("same reply text already used twice today (spam-like)")
        return out

    def _maybe_hide(self, cid, confidence):
        mode = self.ctx.autonomy("hide_spam_comment")
        if mode == "auto" and confidence >= self.cfg["thresholds"]["auto_reply"]:
            try:
                self.client.hide_comment(cid, True)
                self.log.record("ENGAGEMENT", f"Hid spam comment {cid}", api_action="POST /{comment-id}?hide=true",
                                confidence=confidence, approval_required=False, ref=cid)
            except InstagramAPIError as e:
                self.log.record("ENGAGEMENT", f"Failed to hide spam {cid}", error=str(e), ref=cid)

    def _decide(self, cid, fields, decision, reason):
        fields = dict(fields, decision=decision.value, decision_reason=reason)
        if decision == D.ESCALATED:
            fields.pop("suggested_reply", None)  # escalations never carry a ready-to-send reply
        self.store.upsert_comment(cid, **fields)
        if decision in (D.PENDING_APPROVAL, D.ESCALATED):
            self.log.record("ENGAGEMENT", f"{'Suggested reply awaiting approval' if decision == D.PENDING_APPROVAL else 'Escalated to human'} "
                            f"for comment {cid}", reason=reason, confidence=fields.get("confidence"),
                            approval_required=True, ref=cid)
        return decision

    def _send(self, cid, fields, text, auto):
        self.store.upsert_comment(cid, **fields)
        if not self.client.dry_run and not self.store.claim_comment_reply(cid):
            return None  # someone else replied between read and write
        try:
            reply_id = self.client.reply_to_comment(cid, text)
        except InstagramAPIError as e:
            self.store.conn.execute("UPDATE comments SET reply_id=NULL WHERE comment_id=?", (cid,))
            fields = dict(fields, suggested_reply=text)
            self.log.record("REPLY", f"Reply to {cid} failed", error=str(e), api_action="POST /{comment-id}/replies", ref=cid)
            return self._decide(cid, fields, D.PENDING_APPROVAL, f"send failed: {type(e).__name__}")
        decision = D.AUTO_REPLY if auto else D.REPLIED
        if self.client.dry_run:
            self.store.upsert_comment(cid, decision=decision.value, suggested_reply=text,
                                      decision_reason="dry-run: reply simulated, not sent")
            self.log.record("REPLY", f"[dry-run] Would reply to {cid}: “{text}”", confidence=fields.get("confidence"),
                            approval_required=not auto, ref=cid)
            return decision
        self.store.upsert_comment(cid, decision=decision.value, reply_id=reply_id, reply_text=text, replied_at=iso(),
                                  decision_reason="auto-replied" if auto else "approved by human")
        self.log.record("REPLY", f"{'Automatically responded' if auto else 'Sent approved reply'} to @{fields.get('username')}: “{text}”",
                        api_action="POST /{comment-id}/replies", result=f"reply {reply_id}",
                        confidence=fields.get("confidence"), approval_required=not auto, ref=cid)
        return decision

    # ------------------------------------------------------------ human actions
    def approve(self, comment_id, text=None):
        row = self.store.get_comment(comment_id)
        if row is None:
            raise KeyError(f"unknown comment {comment_id}")
        if row["decision"] not in (D.PENDING_APPROVAL.value, D.ESCALATED.value):
            raise ValueError(f"comment {comment_id} is {row['decision']}; nothing to approve")
        if row["reply_id"]:
            raise ValueError(f"comment {comment_id} already has a reply")
        text = (text or row["suggested_reply"] or "").strip()
        if not text:
            raise ValueError("no reply text: pass one with --text")
        fields = {k: row[k] for k in ("media_id", "username", "confidence")}
        return self._send(comment_id, fields, text, auto=False)

    def reject(self, comment_id, note=None):
        row = self.store.get_comment(comment_id)
        if row is None:
            raise KeyError(f"unknown comment {comment_id}")
        self.store.upsert_comment(comment_id, decision=D.REJECTED.value, decision_reason=note or "rejected by human")
        self.log.record("ENGAGEMENT", f"Human rejected reply for {comment_id}", reason=note, approval_required=True,
                        ref=comment_id)
