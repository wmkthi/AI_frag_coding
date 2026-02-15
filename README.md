# AI_frag_coding
coding tool for AI response analysis


# AI Response Coding Tool (Streamlit)

## What it does
- Upload a **CSV** or **Excel (.xlsx)** file.
- Previews the first rows/columns.
- Lets you code each row's **ai_response** (with context from **previous_conversation** and **current_user_turn**) into these columns:

AvoidanceOfFragmentation, DisciplinaryAnchoring, DisciplinaryPedagogicAlignment,
PropositionalCoherence, ReferentialCoherence, RepairOfFragmentation,
SequentialCoherence, ViolationFlag

- If a column already contains **1** in your uploaded file, the checkbox is pre-filled.
- You can navigate row-by-row, jump to the next/previous uncoded row, edit and re-save.
- Download the updated file as **Excel** or **CSV**, preserving all original columns.

## Install & run
1) Install dependencies:
   pip install streamlit pandas openpyxl

2) Run:
   streamlit run app_streamlit_coding_tool.py

## Notes
- The tool keeps "uncoded" as blank/NA (not 0).
- It preserves *all* columns from the uploaded file, adding any missing label columns if needed.

