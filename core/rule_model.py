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
class PresentmentFacts:
    """呈报表事实模型：基于 PDF/OCR 文本按字段锚点容错解析。"""
    presentment_type: str = "unknown"
    page_no: int | None = None
    group_unit: str | None = None
    visit_destination: str | None = None
    transit_destination: str | None = None
    duration_days: int | None = None
    invite_unit_cn: str | None = None
    invite_unit_foreign: str | None = None
    expense_source: str | None = None
    visit_reason: str | None = None
    traveler_names: set[str] = field(default_factory=set)


@dataclass
class BudgetFacts:
    """预算审批意见表事实模型：基于 PDF/OCR 文本按字段锚点容错解析。"""
    page_no: int | None = None
    unit_name: str | None = None
    person_name: str | None = None
    position: str | None = None
    group_name: str | None = None
    group_unit: str | None = None
    leader_level: str | None = None
    group_size: str | None = None
    visit_countries: str | None = None
    duration_days: int | None = None
    plan_included: str | None = None
    time_country_compliant: str | None = None
    route_compliant: str | None = None
    group_size_compliant: str | None = None
    annual_budget_included: str | None = None
    total_cost: str | None = None
    international_travel_cost: str | None = None
    accommodation_cost: str | None = None
    meal_cost: str | None = None
    miscellaneous_cost: str | None = None
    other_cost: str | None = None
    pre_approval_items: str | None = None
    other_matters: str | None = None


@dataclass
class DocumentFacts:
    """材料事实层：只承载已提取事实，不直接表达违规结论。"""
    page_texts: list[tuple[int, str]]
    full_text: str
    pre_invitation_text: str
    profile: GroupProfile
    presentment: PresentmentFacts = field(default_factory=PresentmentFacts)
    budget: BudgetFacts = field(default_factory=BudgetFacts)
    group_unit_name: str | None = None
    has_personnel_list: bool = False
    personnel_names: dict[str, int] = field(default_factory=dict)
    personnel_birth_dates: dict[str, tuple[int, str]] = field(default_factory=dict)
    invitation_birth_dates: dict[str, tuple[int, str]] = field(default_factory=dict)
    has_public_notice: bool = False
    has_translation_info: bool = False
    transport_refs: list[tuple[int, str]] = field(default_factory=list)
    schedule_weekday_mismatches: list[dict[str, object]] = field(default_factory=list)
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
        "personnel.cross_material_consistency",
        "personnel.birth_date_consistency",
        "invite_unit.chinese_name_consistency",
        "schedule.weekday_consistency",
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
