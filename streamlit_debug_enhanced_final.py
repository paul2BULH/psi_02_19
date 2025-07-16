
from PSI_02_19_Patched_POA_All import PSICalculator
import streamlit as st
import pandas as pd
import json

st.set_page_config(page_title="PSI Analyzer", layout="wide")
st.title("🧬 Patient Safety Indicator (PSI) Analyzer")

if 'results_df' not in st.session_state:
    st.session_state.results_df = None
if 'error_df' not in st.session_state:
    st.session_state.error_df = None
if 'analysis_complete' not in st.session_state:
    st.session_state.analysis_complete = False

PSI_CODES = [f"PSI_{i:02}" for i in range(2, 20) if i != 16]

# GLOBAL debug storage
if 'debug_reports' not in st.session_state:
    st.session_state.debug_reports = {}

# Extended calculator with debug
class DebugPSICalculator(PSICalculator):
    def debug_forensic_report(self, row, psi_code, status, rationale, checklist=[], gemini=None):
        report = {
            "encounter_id": row.get("EncounterID", "UNKNOWN"),
            "psi_id": psi_code,
            "status": status,
            "short_rationale": rationale,
            "matched_checklist": checklist,
            "debug_trace": self._generate_debug_trace(row, psi_code, status, rationale),
            "gemini_explanation": gemini or ""
        }
        return report

    def _generate_debug_trace(self, row, psi_code, status, rationale):
        trace = [f"=== FORENSIC DEBUG: EncounterID {row.get('EncounterID')} | PSI {psi_code} ===",
                 f"Status: {status}",
                 f"Rationale: {rationale}",
                 "--- Key Fields ---"]
        for k, v in row.items():
            trace.append(f"{k}: {v}")
        return "\n".join(trace)

# File uploader
uploaded_file = st.file_uploader("📤 Upload PSI Input Excel", type=["xlsx"])
if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file, dtype=str)
        df.fillna("", inplace=True)
        errors = []
    except Exception as e:
        df = pd.DataFrame()
        errors = [str(e)]

    if not errors and not df.empty:
        result_rows = []
        grouped_results = {}
        calc = DebugPSICalculator()

        for idx, row in df.iterrows():
            for psi in PSI_CODES:
                # Unpack PSI result tuple (status, rationale, checklist)
                status, rationale, checklist = calc.evaluate_psi(row, psi)

                report = calc.debug_forensic_report(row, psi, status, rationale, checklist)
                eid = report["encounter_id"]
                grouped_results.setdefault(eid, []).append(report)

        # Display Enhanced UI
        st.header("🔍 Encounter Results (Expandable View)")
        for eid, psi_list in grouped_results.items():
            with st.expander(f"Encounter {eid}", expanded=False):
                for psi in psi_list:
                    st.markdown(f"### PSI: {psi['psi_id']} — **{psi['status']}**")
                    st.markdown(f"🧾 _Rationale:_ {psi['short_rationale']}")

                    with st.expander("✅ Checklist Matches"):
                        st.json(psi["matched_checklist"])

                    with st.expander("🔬 Debug Trace"):
                        st.code(psi["debug_trace"])

                    if psi.get("gemini_explanation"):
                        with st.expander("🤖 Gemini Explanation"):
                            st.markdown(psi["gemini_explanation"])
    else:
        st.error("Errors occurred during Excel load.")
        st.write(errors)
