from collections.abc import Callable
from dataclasses import dataclass, field

from .auditor import AuditIssue


@dataclass
class GroupProfile:
    """团组画像，用于决定审核策略和规则适用范围。"""
    group_type: str
    academic_modes: list[str] = field(default_factory=list)
    is_enterprise: bool = False
    is_academic: bool = False
    is_long_term_visiting: bool = False
    is_academic_exchange_group: bool = False


@dataclass
class DocumentFacts:
    """材料事实层：只承载已提取事实，不直接表达违规结论。"""
    page_texts: list[tuple[int, str]]
    full_text: str
    pre_invitation_text: str
    profile: GroupProfile
    has_personnel_list: bool = False
    has_public_notice: bool = False
    has_translation_info: bool = False
    transport_refs: list[tuple[int, str]] = field(default_factory=list)
    duration_values: dict[str, tuple[int, int]] = field(default_factory=dict)
    invite_units: dict[str, tuple[int, set[str]]] = field(default_factory=dict)
    dispatch_source: str | None = None
    dispatch_material: str | None = None
    expense_source: str | None = None
    has_academic_group_marker: bool = False


@dataclass(frozen=True)
class RuleMetadata:
    """规则元数据，使规则具备可解释、可开关、可测试的基础身份。"""
    id: str
    name: str
    layer: str
    severity: str
    applies_to: tuple[str, ...]
    requires_facts: tuple[str, ...] = ()
    explanation_template: str = ""
    enabled: bool = True
    version: str = "1.0"


RuleDecision = Callable[[DocumentFacts], list[AuditIssue]]


@dataclass(frozen=True)
class AuditPolicy:
    """团组类型驱动的审核策略，显式声明启用和关闭的规则。"""
    group_type: str
    enabled_rule_ids: tuple[str, ...]
    disabled_rule_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def enables(self, rule_id: str) -> bool:
        return rule_id in self.enabled_rule_ids and rule_id not in self.disabled_rule_ids


@dataclass(frozen=True)
class Rule:
    metadata: RuleMetadata
    decision: RuleDecision

    def applies_to(self, facts: DocumentFacts) -> bool:
        if not self.metadata.enabled:
            return False
        return facts.profile.group_type in self.metadata.applies_to

    def evaluate(self, facts: DocumentFacts) -> list[AuditIssue]:
        if not self.applies_to(facts):
            return []
        return self.decision(facts)


class RuleRunner:
    def __init__(self, rules: list[Rule]):
        self.rules = rules

    def run(self, facts: DocumentFacts, policy: AuditPolicy | None = None) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        for rule in self.rules:
            if policy and not policy.enables(rule.metadata.id):
                continue
            issues.extend(rule.evaluate(facts))
        return issues


class PolicySelector:
    COMMON_RULE_IDS = (
        "text.banned_words",
        "duration.cross_material_consistency",
        "invite_unit.chinese_name_consistency",
    )

    @classmethod
    def select(cls, profile: GroupProfile) -> AuditPolicy:
        if profile.group_type == "academic":
            return AuditPolicy(
                group_type=profile.group_type,
                enabled_rule_ids=cls.COMMON_RULE_IDS + ("academic_group.policy_checks",),
                notes=("按高校科研院所团组策略执行审核规则。",),
            )

        if profile.group_type == "enterprise":
            return AuditPolicy(
                group_type=profile.group_type,
                enabled_rule_ids=cls.COMMON_RULE_IDS,
                notes=("按企业团组策略执行审核规则。",),
            )

        return AuditPolicy(
            group_type=profile.group_type,
            enabled_rule_ids=cls.COMMON_RULE_IDS,
            notes=("按普通机关事业单位团组策略执行审核规则。",),
        )
