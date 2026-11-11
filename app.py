import pandas as pd
import plotly.express as px
import streamlit as st

from extractor import load_cache, save_cache, process_messages
from gmail_service import get_gmail_service, build_query, search_emails

st.set_page_config(page_title="Job Application Tracker", layout="wide")

STATUSES = ["Applied", "Online Test", "Interview", "Offer", "Rejected", "Withdrawn"]
EDITABLE_FIELDS = ["company", "job_title", "status", "platform", "location", "notes"]
MAX_EMAILS_TO_SCAN = 300  # raise this if you have a lot of emails to scan


# ---------- Load data ----------

def load_applications():
    cache = load_cache()
    rows = [a for a in cache.values() if a.get("is_application") and not a.get("deleted")]
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    if "notes" not in df.columns:
        df["notes"] = ""
    df["notes"] = df["notes"].fillna("")
    df["date_applied"] = pd.to_datetime(df["date_applied"], errors="coerce")

    return df.sort_values("date_applied", ascending=False).set_index("email_id")


# ---------- Header ----------

title_col, sync_col = st.columns([5, 1])
with title_col:
    st.title("Job Application Tracker")
with sync_col:
    st.write("")
    if st.button("Sync Gmail", width="stretch"):
        with st.spinner("Syncing..."):
            before = len(load_cache())
            service = get_gmail_service()
            ids = search_emails(service, build_query(), max_results=MAX_EMAILS_TO_SCAN)
            process_messages(service, ids)
            new_count = len(load_cache()) - before
        st.toast(f"{new_count} new emails processed")

if st.session_state.pop("saved", False):
    st.success("Changes saved")

df = load_applications()
if df.empty:
    st.info("No applications yet. Click Sync Gmail to get started.")
    st.stop()

hide_dupes = st.toggle("Hide duplicates", value=True)

view = df
if hide_dupes:
    view = view[~view.duplicated(subset=["company", "job_title", "date_applied"], keep="first")]

# Counts
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total", len(view))
m2.metric("Applied", (view["status"] == "Applied").sum())
m3.metric("In progress", view["status"].isin(["Online Test", "Interview"]).sum())
m4.metric("Offers", (view["status"] == "Offer").sum())
m5.metric("Rejected", (view["status"] == "Rejected").sum())

st.divider()

# ---------- Per-column filters ----------

with st.expander("Filter columns"):
    f1, f2, f3, f4, f5 = st.columns(5)
    company_filter = f1.multiselect("Company", sorted(view["company"].dropna().unique()))
    title_filter = f2.multiselect("Job title", sorted(view["job_title"].dropna().unique()))
    status_filter = f3.multiselect("Status", sorted(view["status"].dropna().unique()))
    platform_filter = f4.multiselect("Platform", sorted(view["platform"].dropna().unique()))
    location_filter = f5.multiselect("Location", sorted(view["location"].dropna().unique()))

filtered = view.copy()
if company_filter:
    filtered = filtered[filtered["company"].isin(company_filter)]
if title_filter:
    filtered = filtered[filtered["job_title"].isin(title_filter)]
if status_filter:
    filtered = filtered[filtered["status"].isin(status_filter)]
if platform_filter:
    filtered = filtered[filtered["platform"].isin(platform_filter)]
if location_filter:
    filtered = filtered[filtered["location"].isin(location_filter)]

# ---------- Editable table ----------

st.subheader(f"Applications ({len(filtered)})")

table = filtered[["company", "job_title", "status", "date_applied", "platform", "location", "notes"]].copy()
table["delete"] = False

row_height = 35
table_height = min(700, row_height * (len(table) + 1) + 10)

edited = st.data_editor(
    table,
    hide_index=True,
    width="stretch",
    height=table_height,
    column_config={
        "company": st.column_config.TextColumn("Company", width="medium"),
        "job_title": st.column_config.TextColumn("Job title", width="large"),
        "status": st.column_config.SelectboxColumn("Status", options=STATUSES, required=True, width="small"),
        "date_applied": st.column_config.DateColumn("Applied on", format="YYYY-MM-DD", width="small"),
        "platform": st.column_config.TextColumn("Platform", width="small"),
        "location": st.column_config.TextColumn("Location", width="small"),
        "notes": st.column_config.TextColumn("Notes", width="large"),
        "delete": st.column_config.CheckboxColumn("Delete", width="small"),
    },
    key="editor",
)

if st.button("Save changes", type="primary"):
    cache = load_cache()

    for email_id, row in edited.iterrows():
        entry = cache[email_id]

        if row["delete"]:
            entry["deleted"] = True  # stays in cache so sync won't add it back
            continue

        for field in EDITABLE_FIELDS:
            entry[field] = "" if pd.isna(row[field]) else row[field]

        date = pd.to_datetime(row["date_applied"], errors="coerce")
        entry["date_applied"] = "" if pd.isna(date) else date.strftime("%Y-%m-%d")

    save_cache(cache)
    st.session_state["saved"] = True
    st.rerun()

# ---------- Status pie chart (below the table) ----------

st.divider()
st.subheader("Application status")

status_counts = filtered["status"].value_counts().reset_index()
status_counts.columns = ["status", "count"]

chart_left, chart_mid, chart_right = st.columns([1, 2, 1])
with chart_mid:
    fig = px.pie(status_counts, names="status", values="count", hole=0.45)
    fig.update_traces(textinfo="label+value")
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig, width="stretch")