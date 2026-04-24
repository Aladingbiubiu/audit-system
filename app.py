import streamlit as st
from pathlib import Path
import json
from datetime import datetime

from config.settings import UPLOADS_DIR, FILE_RETENTION_DAYS, ANTHROPIC_API_KEY, ZHIPU_API_KEY, USE_ZHIPU
from core.workflow import WorkflowManager, AuditStatus
from core.pdf_parser import validate_pdf
from core.learning import load_guidelines, save_guideline

st.set_page_config(
    page_title="行政审批材料审核系统",
    page_icon="📋",
    layout="wide"
)

st.markdown("""
<style>
    .stApp { max-width: 1200px; margin: 0 auto; }
    .issue-critical { background-color: #ffebee; padding: 10px; border-radius: 5px; border-left: 4px solid #f44336; margin: 5px 0; }
    .issue-warning { background-color: #fff3e0; padding: 10px; border-radius: 5px; border-left: 4px solid #ff9800; margin: 5px 0; }
    .issue-info { background-color: #e3f2fd; padding: 10px; border-radius: 5px; border-left: 4px solid #2196f3; margin: 5px 0; }
    .status-badge { padding: 4px 12px; border-radius: 12px; font-size: 14px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)


def get_workflow():
    if "workflow" not in st.session_state:
        st.session_state.workflow = WorkflowManager()
    return st.session_state.workflow


def format_status(status: str) -> str:
    status_map = {
        "pending": "⏳ 待审核",
        "auditing": "🔍 审核中",
        "passed": "✅ 已通过",
        "rejected": "❌ 未通过"
    }
    return status_map.get(status, status)


def display_issues(issues: list):
    for issue in issues:
        severity = issue.get("severity", "一般")
        css_class = "issue-critical" if severity == "严重" else "issue-warning" if severity == "一般" else "issue-info"

        st.markdown(f"""
        <div class="{css_class}">
            <strong>[{severity}] {issue.get('category', '')}</strong><br/>
            {issue.get('description', '')}
            {f"<br/><small>位置: {issue['location']}</small>" if issue.get('location') else ""}
        </div>
        """, unsafe_allow_html=True)


def issue_to_label(index: int, issue: dict) -> str:
    severity = issue.get("severity", "")
    category = issue.get("category", "")
    description = issue.get("description", "")
    return f"{index + 1}. [{severity}] {category} - {description[:40]}"


def render_feedback_form(record_id: int, issues: list[dict]):
    workflow = get_workflow()
    st.markdown("#### 反馈学习")

    issue_options = ["整体结论"] + [
        issue_to_label(index, issue)
        for index, issue in enumerate(issues or [])
    ]

    with st.form(f"feedback_form_{record_id}"):
        selected_label = st.selectbox("选择要反馈的问题", issue_options)
        selected_issue = {}
        if selected_label != "整体结论":
            selected_index = issue_options.index(selected_label) - 1
            selected_issue = issues[selected_index]

        case_summary = st.text_input(
            "案例摘要",
            value=selected_issue.get("description", "")[:120]
        )
        human_decision = st.selectbox(
            "人工决定",
            ["采纳AI判断", "不作为问题", "改为严重", "改为一般", "改为提示", "补充问题"]
        )
        human_reason = st.text_area(
            "人工判断理由",
            placeholder="例如：企业团组不强制写明“不是学术交流团”，出访目的已体现为商务拜访。"
        )
        learn_as_guideline = st.checkbox("以后遇到类似情况也按这个口径处理")

        guideline_title = ""
        guideline_applies_when = ""
        guideline_text = ""
        severity_override = ""
        if learn_as_guideline:
            guideline_title = st.text_input("口径标题", value=case_summary[:40])
            guideline_applies_when = st.text_area(
                "适用场景",
                value=case_summary,
                placeholder="描述什么情况下适用这条口径"
            )
            guideline_text = st.text_area(
                "长期口径",
                value=human_reason,
                placeholder="描述以后应如何判断"
            )
            severity_override = st.text_input("处理方式", value=human_decision)

        submitted = st.form_submit_button("保存反馈")

    if submitted:
        ai_issue = json.dumps(selected_issue, ensure_ascii=False) if selected_issue else "整体结论"
        result = workflow.save_feedback(
            record_id=record_id,
            case_summary=case_summary,
            ai_issue=ai_issue,
            human_decision=human_decision,
            human_reason=human_reason,
            learn_as_guideline=learn_as_guideline,
            guideline_title=guideline_title,
            guideline_applies_when=guideline_applies_when,
            guideline_text=guideline_text,
            severity_override=severity_override
        )
        if result["success"]:
            st.success("反馈已保存，后续审核会参考这条判例。")
            if learn_as_guideline:
                st.success("已沉淀为长期审核口径。")
        else:
            st.error(result["error"])


def page_upload():
    st.header("📤 上传材料")

    if USE_ZHIPU:
        st.success("🟢 已连接智谱 GLM API")
    elif ANTHROPIC_API_KEY:
        st.success("🟢 已连接 Claude API")
    else:
        st.error("⚠️ 未检测到 API 密钥，请先设置 ZHIPU_API_KEY 或 ANTHROPIC_API_KEY")
        st.info("在 .env 文件中设置：\n- ZHIPU_API_KEY=你的智谱密钥\n- 或 ANTHROPIC_API_KEY=你的Claude密钥")
        return

    uploaded_file = st.file_uploader(
        "上传PDF文件",
        type=["pdf"],
        help=f"支持最大20MB的PDF文件，文件将保留{FILE_RETENTION_DAYS}天"
    )

    if uploaded_file:
        st.info(f"📄 文件: {uploaded_file.name} ({uploaded_file.size / 1024:.1f} KB)")

        if st.button("开始审核", type="primary", use_container_width=True):
            with st.spinner("正在处理..."):
                save_path = UPLOADS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uploaded_file.name}"
                with open(save_path, "wb") as f:
                    f.write(uploaded_file.getvalue())

                workflow = get_workflow()
                upload_result = workflow.upload_document(save_path)

                if not upload_result["success"]:
                    st.error(f"上传失败: {upload_result['error']}")
                    return

                record_id = upload_result["record_id"]
                st.success(f"上传成功！记录ID: {record_id}")

                metadata = upload_result.get("metadata", {})
                image_only_pages = metadata.get("image_only_pages", [])
                if image_only_pages:
                    pages = "、".join(str(page) for page in image_only_pages)
                    st.warning(
                        f"检测到第 {pages} 页疑似扫描图片页，系统会在审核时自动进行 OCR 识别。"
                        "如扫描质量较差，仍建议人工核对这些页面。"
                    )

                with st.spinner("正在进行AI审核，请稍候..."):
                    audit_result = workflow.start_audit(record_id)

                if not audit_result["success"]:
                    st.error(f"审核失败: {audit_result['error']}")
                    return

                result = audit_result["result"]

                st.markdown("---")
                st.subheader("📋 审核结果")

                if result["passed"]:
                    st.success("✅ 审核通过！材料符合要求。")
                else:
                    st.error(f"❌ 审核未通过，发现 {len(result['issues'])} 个问题。")

                if result["issues"]:
                    st.markdown("#### 问题列表")
                    display_issues(result["issues"])

                st.markdown("#### 总结与建议")
                st.info(result["summary"])

                render_feedback_form(record_id, result.get("issues", []))

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("确认通过", type="primary", key=f"approve_{record_id}"):
                        workflow.approve(record_id)
                        st.success("已确认通过！")
                        st.rerun()

                with col2:
                    reason = st.text_input("退回原因", key=f"reason_{record_id}")
                    if st.button("退回修改", key=f"reject_{record_id}"):
                        if reason:
                            workflow.reject(record_id, reason)
                            st.warning("已退回修改")
                            st.rerun()
                        else:
                            st.warning("请填写退回原因")


def page_records():
    st.header("📋 审核记录")

    workflow = get_workflow()

    status_filter = st.selectbox(
        "筛选状态",
        ["全部", "待审核", "审核中", "已通过", "未通过"],
        index=0
    )

    status_map = {
        "全部": None,
        "待审核": "pending",
        "审核中": "auditing",
        "已通过": "passed",
        "未通过": "rejected"
    }

    records = workflow.list_records(status=status_map[status_filter])

    if not records:
        st.info("暂无审核记录")
        return

    for record in records:
        with st.expander(
            f"{format_status(record['status'])} - {record['filename']} ({record['upload_time'][:10]})",
            expanded=False
        ):
            col1, col2 = st.columns([2, 1])

            with col1:
                st.markdown(f"**记录ID:** {record['id']}")
                st.markdown(f"**上传时间:** {record['upload_time']}")
                if record['completed_time']:
                    st.markdown(f"**完成时间:** {record['completed_time']}")

            with col2:
                if st.button("查看详情", key=f"view_{record['id']}"):
                    st.session_state.selected_record = record['id']
                    st.rerun()

    if "selected_record" in st.session_state:
        st.markdown("---")
        st.subheader("详情")

        record = workflow.get_record(st.session_state.selected_record)
        if record:
            st.json(record)
            if record.get("result"):
                render_feedback_form(record["id"], record["result"].get("issues", []))


def page_rules():
    st.header("⚙️ 规则配置")

    rules_file = Path(__file__).parent / "config" / "rules.yaml"

    if rules_file.exists():
        current_rules = rules_file.read_text(encoding="utf-8")
    else:
        current_rules = "# 请在此输入审核规则"

    new_rules = st.text_area(
        "编辑审核规则 (YAML格式)",
        value=current_rules,
        height=400
    )

    col1, col2 = st.columns(2)

    with col1:
        if st.button("保存规则", type="primary"):
            try:
                import yaml
                yaml.safe_load(new_rules)
                rules_file.write_text(new_rules, encoding="utf-8")
                st.success("规则已保存！")
            except yaml.YAMLError as e:
                st.error(f"YAML格式错误: {e}")

    with col2:
        if st.button("重置为默认"):
            st.rerun()

    st.markdown("---")
    st.markdown("""
    ### 规则配置说明

    规则以YAML格式配置，结构如下：
    ```yaml
    categories:
      - name: "类别名称"
        rules:
          - "规则1"
          - "规则2"
    custom_rules:
      - "自定义规则"
    ```
    """)


def page_learning():
    st.header("🧠 反馈学习")

    workflow = get_workflow()

    st.subheader("长期审核口径")
    guidelines = load_guidelines()
    if not guidelines:
        st.info("暂无长期口径")
    else:
        for item in guidelines:
            with st.expander(item.get("title", "未命名口径")):
                st.markdown(f"**适用场景:** {item.get('applies_when', '')}")
                st.markdown(f"**判断口径:** {item.get('guidance', '')}")
                st.markdown(f"**处理方式:** {item.get('severity_override', '')}")
                st.caption(f"来源: {item.get('source', '')}")

    st.markdown("#### 手动新增口径")
    with st.form("manual_guideline_form"):
        title = st.text_input("标题")
        applies_when = st.text_area("适用场景")
        guidance = st.text_area("判断口径")
        severity_override = st.text_input("处理方式", value="按口径判断")
        submitted = st.form_submit_button("保存口径")

    if submitted:
        if title and applies_when and guidance:
            save_guideline(title, applies_when, guidance, severity_override, source="manual")
            st.success("口径已保存")
            st.rerun()
        else:
            st.error("请填写标题、适用场景和判断口径")

    st.markdown("---")
    st.subheader("最近反馈判例")
    cases = workflow.list_review_cases()
    if not cases:
        st.info("暂无反馈判例")
        return

    for case in cases:
        with st.expander(f"#{case['id']} {case['case_summary'][:50]}"):
            st.markdown(f"**人工决定:** {case['human_decision']}")
            st.markdown(f"**理由:** {case['human_reason']}")
            st.markdown(f"**AI原判断:** {case['ai_issue']}")
            st.caption(f"记录ID: {case['record_id']} | 时间: {case['created_at']}")


def main():
    st.title("📋 行政审批材料审核系统")

    tab1, tab2, tab3, tab4 = st.tabs(["📤 上传审核", "📋 审核记录", "⚙️ 规则配置", "🧠 反馈学习"])

    with tab1:
        page_upload()

    with tab2:
        page_records()

    with tab3:
        page_rules()

    with tab4:
        page_learning()


if __name__ == "__main__":
    main()
