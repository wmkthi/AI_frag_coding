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

TEXT_COLS = ["previous_conversation", "current_user_turn", "ai_response"]


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
    return str(x)


def _write_row_from_session(df: pd.DataFrame, idx: int):
    """Write current text-input states into df exactly as typed (blank => blank)."""
    for c in LABEL_COLS:
        key = f"in_{c}"
        val = st.session_state.get(key, "")
        # Keep blank as blank; otherwise store as-is (string)
        df.at[idx, c] = val if str(val).strip() != "" else ""


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
st.title("AI Response Coding Tool (Free-text coding)")

with st.sidebar:
    st.header("1) Upload your file")
    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])

    st.markdown("---")
    st.header("2) Download")
    st.caption("After coding, download an updated copy of your file.")

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

    st.session_state.df = df
    st.session_state.loaded_filename = uploaded.name
    st.session_state.row_idx = 0

    # Initialize inputs from first row
    _sync_session_from_df(df, 0)
    st.session_state.last_row_idx_for_inputs = 0

df: pd.DataFrame = st.session_state.df
_reset_navigation_if_needed(df)
idx = st.session_state.row_idx

# Top preview
st.subheader("Preview (first 10 rows)")
st.dataframe(df.head(10), use_container_width=True)

st.markdown("---")

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
    if st.button("💾 Save row", use_container_width=True):
        _write_row_from_session(df, idx)
        st.success(f"Saved row {idx+1}.")

with nav5:
    # Count rows where any label cell is non-empty
    coded_count = 0
    for r in range(len(df)):
        if any(str(_safe_text(df.loc[r, c])).strip() != "" for c in LABEL_COLS):
            coded_count += 1
    st.metric("Rows w/ any code", f"{coded_count}/{len(df)}")

st.markdown("---")

# Main content area
left, right = st.columns([2.2, 1.2], gap="large")

with left:
    st.subheader(f"Row {idx+1} of {len(df)}")

    if "id" in df.columns:
        st.caption(f"id: {_safe_text(df.loc[idx, 'id'])}")

    st.markdown("#### previous_conversation")
    st.text_area(
        label="previous_conversation",
        value=_safe_text(df.loc[idx, "previous_conversation"]),
        height=220,
        disabled=True,
    )

    st.markdown("#### current_user_turn")
    st.text_area(
        label="current_user_turn",
        value=_safe_text(df.loc[idx, "current_user_turn"]),
        height=160,
        disabled=True,
    )

    st.markdown("#### ai_response")
    st.text_area(
        label="ai_response",
        value=_safe_text(df.loc[idx, "ai_response"]),
        height=220,
        disabled=True,
    )

    if "Notes" in df.columns:
        st.markdown("#### Notes (editable)")
        new_notes = st.text_area(
            label="Notes",
            value=_safe_text(df.loc[idx, "Notes"]),
            height=120,
            key="ta_notes_edit",
        )
        df.at[idx, "Notes"] = new_notes

with right:
    st.subheader("Coding values (type anything)")

    # Ensure right-side inputs match current row when row changes
    if st.session_state.get("last_row_idx_for_inputs") != idx:
        _sync_session_from_df(df, idx)
        st.session_state.last_row_idx_for_inputs = idx

    st.caption("Leave blank if not applicable. Your exact text is saved into the cell.")

    for c in LABEL_COLS:
        st.text_input(c, key=f"in_{c}")

    st.markdown("---")

    if st.button("💾 Save row (right)", use_container_width=True):
        _write_row_from_session(df, idx)
        st.success(f"Saved row {idx+1}.")

    st.markdown("**Quick actions**")
    colA, colB = st.columns(2)
    with colA:
        if st.button("Clear all", use_container_width=True):
            for c in LABEL_COLS:
                st.session_state[f"in_{c}"] = ""
            _write_row_from_session(df, idx)
            st.success("Cleared codes for this row.")
            st.rerun()
    with colB:
        if st.button("Copy prev row codes", use_container_width=True, disabled=(idx <= 0)):
            for c in LABEL_COLS:
                st.session_state[f"in_{c}"] = _safe_cell_as_string(df.loc[idx - 1, c])
            _write_row_from_session(df, idx)
            st.success("Copied previous row's codes.")
            st.rerun()

# -----------------------------
# Download section (sidebar)
# -----------------------------
with st.sidebar:
    st.markdown("---")
    st.subheader("Download updated file")

    if st.button("Prepare download (save current row)"):
        _write_row_from_session(df, idx)
        st.success("Saved current row changes.")

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

st.caption("Tip: Use Save row before navigating if you want to be extra safe (navigation also saves automatically).")
