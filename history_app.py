from __future__ import annotations

from pathlib import Path

import streamlit as st

from history.importer import ImportSummary, TripBatchImporter
from history.models import HISTORY_DATABASE_URL
from history.recommender import TripRecommender
from history.repository import HistoryRepository, record_to_dict


st.set_page_config(
    page_title="历史出访知识库",
    page_icon="📚",
    layout="wide",
)


def get_repository() -> HistoryRepository:
    if "history_repository" not in st.session_state:
        st.session_state.history_repository = HistoryRepository()
    return st.session_state.history_repository


def render_import_page() -> None:
    st.header("批量建库")
    st.caption(f"独立数据库：{HISTORY_DATABASE_URL}")

    root_path = st.text_input("历史材料根目录", placeholder=r"D:\历史出访材料")
    col1, col2 = st.columns([1, 1])
    with col1:
        enable_ocr = st.checkbox("启用 OCR", value=True)
    with col2:
        enable_llm = st.checkbox("启用 LLM 辅助补充", value=False, help="批量材料较多时会增加耗时和 API 成本，默认关闭。")

    if st.button("开始递归导入 PDF", type="primary", use_container_width=True):
        if not root_path.strip():
            st.warning("请先输入历史材料根目录。")
            return
        if not Path(root_path).exists():
            st.error("目录不存在，请检查路径。")
            return

        progress = st.progress(0)
        status_box = st.empty()
        result_box = st.empty()

        def update_progress(summary: ImportSummary) -> None:
            ratio = summary.success_count + summary.failed_count
            total = max(summary.total_files, 1)
            progress.progress(min(ratio / total, 1.0))
            status_box.info(
                f"已处理 {ratio}/{summary.total_files}，成功 {summary.success_count}，"
                f"失败 {summary.failed_count}，需复核 {summary.review_count}"
            )

        with st.spinner("正在批量解析 PDF，请保持页面打开..."):
            importer = TripBatchImporter(repository=get_repository())
            summary = importer.import_root(
                root_path,
                enable_ocr=enable_ocr,
                enable_llm=enable_llm,
                progress_callback=update_progress,
            )

        st.session_state.last_import_summary = summary
        result_box.success(
            f"导入完成：共 {summary.total_files} 个 PDF，成功 {summary.success_count}，"
            f"失败 {summary.failed_count}，需复核 {summary.review_count}。"
        )

    if "last_import_summary" in st.session_state:
        render_import_summary(st.session_state.last_import_summary)

    st.markdown("---")
    render_import_jobs()


def render_import_summary(summary: ImportSummary) -> None:
    with st.expander("本次导入明细", expanded=True):
        for item in summary.results[-100:]:
            label = f"{item.status} | {item.duplicate_status} | {item.path}"
            if item.status == "failed":
                st.error(f"{label}\n\n{item.message}")
            elif item.status == "needs_review":
                st.warning(label if not item.message else f"{label}\n\n{item.message}")
            else:
                st.success(label)


def render_import_jobs() -> None:
    repo = get_repository()
    st.subheader("最近导入任务")
    jobs = repo.list_import_jobs()
    if not jobs:
        st.info("暂无导入任务。")
        return
    for job in jobs:
        with st.expander(f"#{job.id} {job.status} - {job.root_path}", expanded=False):
            st.write(
                {
                    "总数": job.total_files,
                    "已处理": job.processed_count,
                    "成功": job.success_count,
                    "失败": job.failed_count,
                    "需复核": job.review_count,
                    "开始时间": job.started_at,
                    "完成时间": job.completed_at,
                    "消息": job.message,
                }
            )


def render_archive_page() -> None:
    st.header("出访档案")
    repo = get_repository()

    with st.form("record_filters"):
        cols = st.columns(5)
        country = cols[0].text_input("国家")
        industry = cols[1].text_input("业务领域")
        organization = cols[2].text_input("国外单位")
        group_unit = cols[3].text_input("组团单位")
        duplicate_status = cols[4].selectbox("重复状态", ["", "unique", "possible_duplicate", "exact_duplicate"])
        search = st.text_input("全文关键词")
        submitted = st.form_submit_button("查询")

    records = repo.list_records(
        country=country or None,
        industry=industry or None,
        organization=organization or None,
        group_unit=group_unit or None,
        duplicate_status=duplicate_status or None,
        search=search or None,
        limit=100,
    )
    if not submitted and not records:
        st.info("暂无历史出访档案。")
        return

    st.caption(f"共显示 {len(records)} 条")
    for record in records:
        title = " | ".join(
            part
            for part in [
                f"#{record.id}",
                record.country or "未识别国家",
                record.organization_name_cn or "未识别国外单位",
                record.industry or "未识别领域",
                record.source_filename or "",
            ]
            if part
        )
        with st.expander(title, expanded=False):
            render_record_detail(record.id)


def render_record_detail(record_id: int) -> None:
    repo = get_repository()
    record = repo.get_record(record_id)
    if not record:
        st.error("记录不存在。")
        return

    col1, col2 = st.columns([2, 1])
    with col1:
        st.write(record_to_dict(record))
        contacts = repo.list_contacts(record.id)
        if contacts:
            st.markdown("**联系人**")
            st.table(
                [
                    {
                        "姓名": contact.name,
                        "职务": contact.title,
                        "邮箱": contact.email,
                        "电话": contact.phone,
                    }
                    for contact in contacts
                ]
            )
    with col2:
        render_record_edit_form(record)


def render_record_edit_form(record) -> None:
    with st.form(f"edit_record_{record.id}"):
        st.markdown("**人工修正**")
        country = st.text_input("国家", value=record.country or "")
        region = st.text_input("地区", value=record.region or "")
        city = st.text_input("城市", value=record.city or "")
        organization_name_cn = st.text_input("国外单位中文名", value=record.organization_name_cn or "")
        organization_name_en = st.text_input("国外单位外文名", value=record.organization_name_en or "")
        industry = st.text_input("业务领域", value=record.industry or "")
        org_type = st.text_input("机构类型", value=record.org_type or "")
        group_unit = st.text_input("组团单位", value=record.group_unit or "")
        visit_date = st.text_input("出访日期", value=record.visit_date or "")
        duration_days = st.number_input("停留天数", min_value=0, value=int(record.duration_days or 0), step=1)
        visit_purpose = st.text_area("出访事由", value=record.visit_purpose or "")
        visit_summary = st.text_area("出访摘要", value=record.visit_summary or "")
        needs_review = st.checkbox("仍需复核", value=bool(record.needs_review))
        duplicate_status = st.selectbox(
            "重复状态",
            ["unique", "possible_duplicate", "exact_duplicate"],
            index=["unique", "possible_duplicate", "exact_duplicate"].index(record.duplicate_status or "unique"),
        )
        duplicate_reason = st.text_area("重复说明", value=record.duplicate_reason or "")

        if st.form_submit_button("保存修正"):
            ok = get_repository().update_record(
                record.id,
                {
                    "country": country,
                    "region": region,
                    "city": city,
                    "organization_name_cn": organization_name_cn,
                    "organization_name_en": organization_name_en,
                    "industry": industry,
                    "org_type": org_type,
                    "group_unit": group_unit,
                    "visit_date": visit_date,
                    "duration_days": int(duration_days) if duration_days else None,
                    "visit_purpose": visit_purpose,
                    "visit_summary": visit_summary,
                    "needs_review": needs_review,
                    "status": "needs_review" if needs_review else "completed",
                    "duplicate_status": duplicate_status,
                    "duplicate_reason": duplicate_reason,
                },
            )
            if ok:
                st.success("已保存。")
                st.rerun()
            else:
                st.error("保存失败。")


def render_recommend_page() -> None:
    st.header("智能推荐")
    country = st.text_input("目标国家", placeholder="例如：德国")
    industry = st.text_input("业务领域偏好（可选）", placeholder="例如：教育科研")

    if st.button("生成推荐", type="primary", use_container_width=True):
        if not country.strip():
            st.warning("请输入目标国家。")
            return
        recommender = TripRecommender(repository=get_repository())
        result = recommender.recommend(country.strip(), industry.strip() or None)
        st.session_state.recommend_result = result

    result = st.session_state.get("recommend_result")
    if not result:
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.subheader("历史出访")
        if not result["history"]:
            st.info("暂无该国家历史记录。")
        for record in result["history"]:
            st.markdown(f"**#{record['id']} {record.get('organization_name_cn') or '未识别单位'}**")
            st.caption(f"{record.get('industry') or '未识别领域'} | {record.get('visit_date') or '未识别日期'}")
            st.write(record.get("visit_summary") or record.get("visit_purpose") or "")

    with col2:
        st.subheader("常见国外单位")
        if not result["organizations"]:
            st.info("暂无国外单位记录。")
        for org in result["organizations"]:
            st.markdown(f"**{org['name_cn']}**")
            st.caption(f"{org.get('industry') or '未识别领域'} | 出现 {org.get('visit_count') or 0} 次")

    with col3:
        st.subheader("相似任务")
        if not result["similar"]:
            st.info("暂无相似任务。")
        for item in result["similar"]:
            record = item["record"]
            st.markdown(f"**#{record['id']} {record.get('country') or ''} {record.get('organization_name_cn') or ''}**")
            st.caption("；".join(item["reasons"]) or f"得分 {item['score']}")
            st.write(record.get("visit_summary") or record.get("visit_purpose") or "")


def main() -> None:
    st.title("历史出访知识库")
    st.caption("独立模块：不接入自动审核流程，不写入 audit.db。")

    tab_import, tab_archive, tab_recommend = st.tabs(["批量建库", "出访档案", "智能推荐"])
    with tab_import:
        render_import_page()
    with tab_archive:
        render_archive_page()
    with tab_recommend:
        render_recommend_page()


if __name__ == "__main__":
    main()

