"""Status enums and the state machines that guard them."""
from enum import Enum


class InvalidTransition(Exception):
    pass


class PublicationStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    QUEUED = "QUEUED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


P = PublicationStatus
PUBLICATION_TRANSITIONS = {
    P.DRAFT: {P.VALIDATED, P.REQUIRES_REVIEW, P.FAILED},
    P.VALIDATED: {P.QUEUED, P.REQUIRES_REVIEW, P.DRAFT},
    P.REQUIRES_REVIEW: {P.DRAFT, P.VALIDATED, P.QUEUED},
    P.QUEUED: {P.PUBLISHING, P.REQUIRES_REVIEW, P.DRAFT},
    # PUBLISHING -> QUEUED only happens when a transient failure is rescheduled.
    P.PUBLISHING: {P.PUBLISHED, P.FAILED, P.QUEUED, P.REQUIRES_REVIEW},
    P.FAILED: {P.DRAFT, P.QUEUED, P.REQUIRES_REVIEW},
    P.PUBLISHED: set(),  # terminal: a published post can never be re-published
}


class ContentType(str, Enum):
    IMAGE = "IMAGE"
    REELS = "REELS"
    CAROUSEL = "CAROUSEL"


class CommentCategory(str, Enum):
    POSITIVE = "positive"
    QUESTION = "question"
    DISAGREEMENT = "disagreement"
    CRITICISM = "criticism"
    SPAM = "spam"
    SALES_INQUIRY = "sales_inquiry"
    POTENTIAL_CUSTOMER = "potential_customer"
    PARTNERSHIP = "partnership"
    AMBIGUOUS = "ambiguous"
    SENSITIVE = "sensitive"


class CommentDecision(str, Enum):
    AUTO_REPLY = "AUTO_REPLY"          # replied autonomously
    PENDING_APPROVAL = "PENDING_APPROVAL"  # suggested reply awaiting a human
    ESCALATED = "ESCALATED"            # human needed, no reply drafted/sent
    IGNORED = "IGNORED"                # e.g. spam, own comment, no reply warranted
    REPLIED = "REPLIED"                # human-approved reply sent
    REJECTED = "REJECTED"              # human rejected the suggestion


class OpportunityStatus(str, Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    DISMISSED = "DISMISSED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"


O = OpportunityStatus
OPPORTUNITY_TRANSITIONS = {
    O.NEW: {O.REVIEWED, O.APPROVED, O.DISMISSED, O.EXPIRED},
    O.REVIEWED: {O.APPROVED, O.DISMISSED, O.EXPIRED},
    O.APPROVED: {O.COMPLETED, O.DISMISSED, O.EXPIRED},
    O.DISMISSED: set(),
    O.COMPLETED: set(),
    O.EXPIRED: set(),
}


def check_transition(table, current, new):
    current, new = type(new)(current), new
    if new not in table[current]:
        raise InvalidTransition(f"{current.value} -> {new.value} is not allowed")
