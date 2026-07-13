# app_streamlit_coding_tool.py
# Streamlit tool for row-by-row human coding of AI responses.
# - Upload CSV/XLSX
# - Navigate rows (Prev/Next/Jump)
# - 8 category text inputs (stores ANY typed value; blank stays blank)
# - Pre-populates from existing coded values
# - Download updated file (XLSX or CSV)

import io
import re
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound

st.set_page_config(page_title="AI Response Coding Tool", layout="wide")

# -----------------------------
# Config
# -----------------------------
LABEL_COLS = [
    "AvoidanceOfFragmentation",
    "DisciplinaryAnchoring",
    "DisciplinaryPedagogicAlignment",
    "PropositionalCoherence",
    "ReferentialCoherence",
    "RepairOfFragmentation",
    "SequentialCoherence",
    "ViolationFlag",
]

TEXT_COLS = ["context", "current_user_turn", "ai_turn"]

LABEL_OPTIONS = ["", "0", "1"]

# Google Sheets used as durable storage for coding progress, so that a
# Streamlit Cloud timeout/restart (which wipes session state and any
# uploaded-but-undownloaded file) never loses coded rows. Requires two
# entries in Streamlit secrets:
#   [gcp_service_account]   <- full service-account JSON key, as a TOML table
#   gsheet_id = "..."       <- the spreadsheet ID from its URL
GSHEET_WORKSHEET_NAME = "coding_progress"
GSHEET_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# -----------------------------
# Helpers
# -----------------------------
def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    # Ensure label columns exist
    for c in LABEL_COLS:
        if c not in df.columns:
            df[c] = ""
    # Ensure text cols exist (if missing, create empty for UI)
    for c in TEXT_COLS:
        if c not in df.columns:
            df[c] = ""
    # Normalize editable columns to plain strings (e.g. 1.0 -> "1", NaN -> "").
    # A numeric dtype inferred from the source file would reject later string
    # writes ("0"/"1"/""), and a mixed float/str object column breaks Arrow
    # serialization for the preview table.
    editable_cols = list(LABEL_COLS)
    if "Notes" in df.columns:
        editable_cols.append("Notes")
    for c in editable_cols:
        df[c] = df[c].apply(_safe_cell_as_string)
    return df


def _load_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(uploaded_file)
    raise ValueError("Unsupported file type. Please upload a CSV or Excel file.")


def _safe_text(x) -> str:
    if pd.isna(x):
        return ""
    return str(x)


def _safe_cell_as_string(x) -> str:
    """
    Convert any existing cell value to a string for editing:
    - NA -> ""
    - numbers/bools/strings -> string form
    """
    if pd.isna(x):
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _id_col(df: pd.DataFrame):
    if "ID" in df.columns:
        return "ID"
    if "id" in df.columns:
        return "id"
    return None


def _gsheet_configured() -> bool:
    # st.secrets raises if no secrets.toml exists at all (not just a KeyError
    # for a missing key), which would otherwise crash the app pre-setup.
    try:
        return "gcp_service_account" in st.secrets and "gsheet_id" in st.secrets
    except Exception:
        return False


@st.cache_resource(show_spinner=False)
def _get_gsheet_worksheet():
    """Authorize with the service account and return the worksheet used to
    persist coding progress, creating it (with a header row) if needed."""
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=GSHEET_SCOPES
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(st.secrets["gsheet_id"])
    try:
        ws = spreadsheet.worksheet(GSHEET_WORKSHEET_NAME)
    except WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=GSHEET_WORKSHEET_NAME, rows=2000, cols=20)
        ws.append_row(["ID"] + LABEL_COLS + ["Notes"])
    return ws


def _apply_gsheet_progress(df: pd.DataFrame, id_col: str):
    """Overlay any previously-saved codes from the Google Sheet onto df.
    Lets coding progress survive a Streamlit Cloud timeout/restart, which
    wipes session state, as long as the same file is re-uploaded afterward."""
    if not id_col or not _gsheet_configured():
        return
    try:
        ws = _get_gsheet_worksheet()
        records = ws.get_all_records()
    except Exception as e:
        st.sidebar.warning(f"Could not load saved progress from Google Sheets: {e}")
        return

    id_to_idx = {str(df.loc[i, id_col]).strip(): i for i in range(len(df))}
    for rec in records:
        row_id = str(rec.get("ID", "")).strip()
        i = id_to_idx.get(row_id)
        if i is None:
            continue
        for c in LABEL_COLS:
            val = str(rec.get(c, "")).strip()
            if val:
                df.at[i, c] = val
        if "Notes" in df.columns:
            notes_val = str(rec.get("Notes", "")).strip()
            if notes_val:
                df.at[i, "Notes"] = notes_val


def _save_row_to_gsheet(df: pd.DataFrame, idx: int, id_col: str):
    """Upsert the current row's codes into the Google Sheet by ID."""
    if not id_col or not _gsheet_configured():
        return
    row_id = str(df.loc[idx, id_col]).strip()
    if not row_id:
        return
    values = (
        [row_id]
        + [str(df.loc[idx, c]) for c in LABEL_COLS]
        + [str(df.loc[idx, "Notes"]) if "Notes" in df.columns else ""]
    )

    try:
        ws = _get_gsheet_worksheet()
        id_map = st.session_state.setdefault("gsheet_id_row_map", {})
        if not id_map:
            for i, v in enumerate(ws.col_values(1)[1:], start=2):
                id_map[v.strip()] = i

        if row_id in id_map:
            ws.update(f"A{id_map[row_id]}", [values])
        else:
            ws.append_row(values)
            id_map[row_id] = len(ws.col_values(1))
    except Exception as e:
        st.sidebar.warning(f"Auto-save to Google Sheets failed for row {idx+1}: {e}")


def _maybe_sync_row_to_gsheet(df: pd.DataFrame, idx: int, id_col: str):
    """Push the row to Google Sheets only if its coded values actually
    changed since the last sync, to avoid a network call on every rerun."""
    if not id_col or not _gsheet_configured():
        return
    current = tuple(str(df.loc[idx, c]) for c in LABEL_COLS)
    if "Notes" in df.columns:
        current += (str(df.loc[idx, "Notes"]),)
    synced = st.session_state.setdefault("gsheet_synced_values", {})
    if synced.get(idx) == current:
        return
    _save_row_to_gsheet(df, idx, id_col)
    synced[idx] = current


def _write_row_from_session(df: pd.DataFrame, idx: int):
    """Write current text-input states into df exactly as typed (blank => blank),
    then auto-save the row to Google Sheets if configured."""
    for c in LABEL_COLS:
        key = f"in_{c}"
        val = st.session_state.get(key, "")
        # Keep blank as blank; otherwise store as-is (string)
        df.at[idx, c] = val if str(val).strip() != "" else ""
    _maybe_sync_row_to_gsheet(df, idx, _id_col(df))


def _sync_session_from_df(df: pd.DataFrame, idx: int):
    """Set session inputs based on df at idx."""
    for c in LABEL_COLS:
        st.session_state[f"in_{c}"] = _safe_cell_as_string(df.loc[idx, c])


def _reset_navigation_if_needed(df: pd.DataFrame):
    if "row_idx" not in st.session_state:
        st.session_state.row_idx = 0
    st.session_state.row_idx = max(0, min(int(st.session_state.row_idx), len(df) - 1))


# -----------------------------
# UI
# -----------------------------
st.markdown(
    """
    <style>
    /* De-emphasize the blank ("—") option, which is always first in LABEL_OPTIONS */
    div[data-testid="stRadio"] label:first-of-type > div:first-of-type {
        opacity: 0.35;
    }
    div[data-testid="stRadio"] label:first-of-type p {
        opacity: 0.35;
        font-size: 0.85em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("AI Response Coding Tool (Free-text coding)")

if not _gsheet_configured():
    st.warning(
        "Google Sheets auto-save is not configured (missing `gcp_service_account` / "
        "`gsheet_id` in Streamlit secrets). Codes will only live in this browser "
        "session and will be lost on a timeout until you download a copy.",
        icon="⚠️",
    )

with st.sidebar:
    st.header("1) Upload your file")
    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])

    st.markdown("---")
    st.header("2) Download")
    st.caption("Codes auto-save to Google Sheets as you go. Download here for a local copy.")

if not uploaded:
    st.info("Upload a CSV/XLSX to start coding.")
    st.stop()

# Load / cache file in session
if "df" not in st.session_state or st.session_state.get("loaded_filename") != uploaded.name:
    try:
        df = _load_file(uploaded)
    except Exception as e:
        st.error(f"Could not read the file: {e}")
        st.stop()

    df = _ensure_columns(df)

    # Restore any codes already saved to Google Sheets from a prior session
    # (e.g. after a Streamlit Cloud timeout wiped this session's state).
    with st.spinner("Checking Google Sheets for previously saved codes..."):
        _apply_gsheet_progress(df, _id_col(df))

    st.session_state.df = df
    st.session_state.loaded_filename = uploaded.name
    st.session_state.row_idx = 0
    st.session_state.gsheet_id_row_map = {}
    st.session_state.gsheet_synced_values = {}

    # Initialize inputs from first row
    _sync_session_from_df(df, 0)
    st.session_state.last_row_idx_for_inputs = 0

df: pd.DataFrame = st.session_state.df
_reset_navigation_if_needed(df)
idx = st.session_state.row_idx

# Navigation controls
nav1, nav2, nav3, nav4, nav5 = st.columns([1, 1, 2, 1, 1], vertical_alignment="center")

with nav1:
    if st.button("⬅ Prev", use_container_width=True, disabled=(idx <= 0)):
        _write_row_from_session(df, idx)
        st.session_state.row_idx = idx - 1
        _sync_session_from_df(df, st.session_state.row_idx)
        st.session_state.last_row_idx_for_inputs = st.session_state.row_idx
        st.rerun()

with nav2:
    if st.button("Next ➡", use_container_width=True, disabled=(idx >= len(df) - 1)):
        _write_row_from_session(df, idx)
        st.session_state.row_idx = idx + 1
        _sync_session_from_df(df, st.session_state.row_idx)
        st.session_state.last_row_idx_for_inputs = st.session_state.row_idx
        st.rerun()

with nav3:
    jump = st.number_input(
        "Jump to row (1-based)",
        min_value=1,
        max_value=len(df),
        value=idx + 1,
        step=1,
    )
    if int(jump) - 1 != idx:
        _write_row_from_session(df, idx)
        st.session_state.row_idx = int(jump) - 1
        _sync_session_from_df(df, st.session_state.row_idx)
        st.session_state.last_row_idx_for_inputs = st.session_state.row_idx
        st.rerun()

with nav4:
    if _gsheet_configured():
        st.caption("✅ Auto-saving to Google Sheets")
    else:
        st.caption("⚠️ Auto-save not configured")

with nav5:
    # Count rows where any label cell is non-empty
    coded_count = 0
    for r in range(len(df)):
        if any(str(_safe_text(df.loc[r, c])).strip() != "" for c in LABEL_COLS):
            coded_count += 1
    st.metric("Rows w/ any code", f"{coded_count}/{len(df)}")

st.markdown("---")

# Main content area
st.subheader(f"Row {idx+1} of {len(df)}")

id_col = _id_col(df)
if id_col:
    st.caption(f"{id_col}: {_safe_text(df.loc[idx, id_col])}")

st.markdown("#### context (previous conversation)")
st.text_area(
    label="context",
    value=_safe_text(df.loc[idx, "context"]),
    height=220,
    disabled=True,
)

turn_left, turn_right = st.columns(2, gap="large")

with turn_left:
    st.markdown("#### current_user_turn")
    st.text_area(
        label="current_user_turn",
        value=_safe_text(df.loc[idx, "current_user_turn"]),
        height=160,
        disabled=True,
    )

with turn_right:
    st.markdown("#### ai_turn (AI response)")
    st.text_area(
        label="ai_turn",
        value=_safe_text(df.loc[idx, "ai_turn"]),
        height=160,
        disabled=True,
    )

if "Notes" in df.columns:
    st.markdown("#### Notes (editable)")
    new_notes = st.text_area(
        label="Notes",
        value=_safe_text(df.loc[idx, "Notes"]),
        height=100,
        key="ta_notes_edit",
    )
    df.at[idx, "Notes"] = new_notes
    _maybe_sync_row_to_gsheet(df, idx, id_col)

st.markdown("---")
st.subheader("Coding values")

# Ensure inputs match current row when row changes
if st.session_state.get("last_row_idx_for_inputs") != idx:
    _sync_session_from_df(df, idx)
    st.session_state.last_row_idx_for_inputs = idx

st.caption("Leave blank if not yet coded. Your picks save automatically.")

half = (len(LABEL_COLS) + 1) // 2
for row_cols in (LABEL_COLS[:half], LABEL_COLS[half:]):
    row_ui = st.columns(len(row_cols), gap="small")
    for col, c in zip(row_ui, row_cols):
        with col:
            st.radio(
                c,
                options=LABEL_OPTIONS,
                key=f"in_{c}",
                format_func=lambda v: "—" if v == "" else v,
                horizontal=True,
            )

# Every rerun (including one triggered by clicking a radio above) flushes
# the current picks into df and, if configured, syncs them to Google Sheets.
_write_row_from_session(df, idx)

st.markdown("---")

clear_col, copy_col = st.columns(2)
with clear_col:
    if st.button("Clear all", use_container_width=True):
        for c in LABEL_COLS:
            st.session_state[f"in_{c}"] = ""
        _write_row_from_session(df, idx)
        st.success("Cleared codes for this row.")
        st.rerun()
with copy_col:
    if st.button("Copy prev row codes", use_container_width=True, disabled=(idx <= 0)):
        for c in LABEL_COLS:
            st.session_state[f"in_{c}"] = _safe_cell_as_string(df.loc[idx - 1, c])
        _write_row_from_session(df, idx)
        st.success("Copied previous row's codes.")
        st.rerun()

st.markdown("---")
st.subheader("Preview (first 10 rows)")
st.dataframe(df.head(10), use_container_width=True)

# -----------------------------
# Download section (sidebar)
# -----------------------------
with st.sidebar:
    st.markdown("---")
    st.subheader("Download updated file")
    st.caption("Reflects your latest picks automatically — no need to save first.")

    # Excel download
    excel_buf = io.BytesIO()
    with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="data")
    excel_buf.seek(0)

    base = re.sub(r"\.(csv|xlsx|xls)$", "", uploaded.name, flags=re.I)

    st.download_button(
        "⬇️ Download as Excel (.xlsx)",
        data=excel_buf,
        file_name=f"{base}_coded.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    # CSV download
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download as CSV (.csv)",
        data=csv_bytes,
        file_name=f"{base}_coded.csv",
        mime="text/csv",
        use_container_width=True,
    )

st.caption(
    "Tip: every pick auto-saves to Google Sheets as soon as you make it — "
    "no need to click Save, and progress survives a session timeout."
    if _gsheet_configured()
    else "Tip: Google Sheets auto-save isn't configured, so download a copy "
    "periodically to avoid losing progress on a timeout."
)
