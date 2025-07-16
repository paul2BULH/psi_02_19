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

# GLOBAL debug storage (by encounter, psi)
if 'debug_reports' not in st.session_state:
    st.session_state.debug_reports = {}

class DebugPSICalculator(PSICalculator):
    def debug_forensic_report(self, row, psi_code, status, rationale):
        """
        Generates a deep forensic debug report for any encounter and PSI.
        """
        report_lines = []
        enc_id = row.get('EncounterID', 'UNKNOWN')
        age = row.get('AGE')
        mdc = row.get('MDC')
        drg = row.get('MS-DRG')
        pdx = row.get('Pdx')
        report_lines.append(f"=== FORENSIC DEBUG: EncounterID {enc_id}, PSI {psi_code} ===")
        report_lines.append(f"Status: {status}")
        report_lines.append(f"Rationale: {rationale}")
        report_lines.append(f"--- Key Fields ---")
        report_lines.append(f"AGE: {age} (type: {type(age)})")
        report_lines.append(f"MDC: {mdc} (type: {type(mdc)})")
        report_lines.append(f"MS-DRG: {drg} (type: {type(drg)})")
        report_lines.append(f"Pdx: '{pdx}' (type: {type(pdx)})")
        if hasattr(self, '_get_all_diagnoses'):
            diagnoses = self._get_all_diagnoses(row)
            report_lines.append(f"All Diagnoses: {diagnoses}")
        if hasattr(self, '_get_all_procedures'):
            procedures = self._get_all_procedures(row)
            report_lines.append(f"All Procedures: {procedures}")
        # Obstetric path (MDC 14)
        try:
            mdc14prindx = self.code_sets.get('MDC14PRINDX', set())
            if pd.notna(mdc) and str(mdc) == "14":
                pdx_str = str(pdx)
                upper_match = pdx_str.strip().upper() in {c.strip().upper() for c in mdc14prindx}
                report_lines.append(f"(Obstetric) MDC==14, Pdx in MDC14PRINDX: {upper_match}")
                report_lines.append(f"Principal DX (normalized): '{pdx_str.strip().upper()}'")
                report_lines.append(f"Sample MDC14PRINDX codes (normalized): {list(sorted({c.strip().upper() for c in mdc14prindx}))[:10]}")
                report_lines.append(f"O10019 in set: {'O10019' in {c.strip().upper() for c in mdc14prindx}}")
        except Exception as e:
            report_lines.append(f"[Obstetric Path Debug Failed: {e}]")
        # DRG/age logic for surgical/medical
        try:
            surg_set = self.code_sets.get('SURGI2R', set())
            med_set = self.code_sets.get('MEDIC2R', set())
            drg_val = str(drg).strip().upper() if pd.notna(drg) else ''
            drg_surg = drg_val in {c.strip().upper() for c in surg_set}
            drg_med = drg_val in {c.strip().upper() for c in med_set}
            report_lines.append(f"Surgical DRG match: {drg_surg}")
            report_lines.append(f"Medical DRG match: {drg_med}")
        except Exception as e:
            report_lines.append(f"[DRG Path Debug Failed: {e}]")
        # Final output
        report_lines.append("=" * 60)
        return "\n".join(report_lines)

    def evaluate_psi(self, row: pd.Series, psi_code: str):
        # Run standard exclusion and logic
        status, rationale, _, _ = super().evaluate_psi(row, psi_code)
        # Save forensic report for this row/PSI if debug mode is enabled
        key = (row.get('EncounterID'), psi_code)
        report = self.debug_forensic_report(row, psi_code, status, rationale)
        st.session_state.debug_reports[key] = report
        return status, rationale, psi_code, {}

def run_psi_analysis(df, calculator, debug_mode=False):
    results = []
    errors = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    st.session_state.debug_reports = {}  # Clear previous debug reports
    total_evaluations = len(df) * len(PSI_CODES)
    current_evaluation = 0

    for idx, row in df.iterrows():
        enc_id = row.get("EncounterID", f"Row{idx+1}")
        status_text.text(f"Processing encounter {idx+1}/{len(df)}: {enc_id}")
        for psi_code in PSI_CODES:
            try:
                status, rationale, _, _ = calculator.evaluate_psi(row, psi_code)
                results.append({
                    "EncounterID": enc_id,
                    "PSI": psi_code,
                    "Status": status,
                    "Rationale": rationale
                })
            except Exception as e:
                errors.append({
                    "EncounterID": enc_id,
                    "PSI": psi_code,
                    "Error": str(e)
                })
            current_evaluation += 1
            progress_bar.progress(current_evaluation / total_evaluations)
    progress_bar.empty()
    status_text.empty()
    return pd.DataFrame(results), pd.DataFrame(errors)

def display_dashboard(df):
    if df is None or "Status" not in df.columns:
        return
    total = len(df)
    inclusions = (df["Status"] == "Inclusion").sum()
    exclusions = (df["Status"] == "Exclusion").sum()
    errors = (df["Status"] == "Error").sum()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Evaluated", total)
    col2.metric("Inclusions", inclusions)
    col3.metric("Exclusions", exclusions)
    col4.metric("Errors", errors)

def display_results_table(results_df, debug_mode=False):
    col1, col2 = st.columns(2)
    with col1:
        psi_filter = st.multiselect("Filter by PSI", sorted(results_df["PSI"].unique()), key="psi_filter")
    with col2:
        status_filter = st.multiselect("Filter by Status", ["Inclusion", "Exclusion", "Error"], key="status_filter")
    filtered_df = results_df.copy()
    if psi_filter:
        filtered_df = filtered_df[filtered_df["PSI"].isin(psi_filter)]
    if status_filter:
        filtered_df = filtered_df[filtered_df["Status"].isin(status_filter)]
    st.write(f"Showing {len(filtered_df)} of {len(results_df)} results")
    st.dataframe(filtered_df, use_container_width=True)
    # Show forensic debug for each row if debug_mode
    if debug_mode and not filtered_df.empty:
        for i, row in filtered_df.iterrows():
            key = (row["EncounterID"], row["PSI"])
            report = st.session_state.debug_reports.get(key)
            with st.expander(f"🔬 Debug: Encounter {row['EncounterID']} | {row['PSI']} | {row['Status']}"):
                st.text(report if report else "No debug report available for this row.")
    csv_data = filtered_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("⬇️ Download Filtered Results", data=csv_data, file_name="PSI_Results.csv")

uploaded_file = st.file_uploader("📂 Upload Excel or CSV File", type=["xlsx", "xls", "csv"])

if uploaded_file:
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        df.columns = df.columns.str.strip()
        st.success(f"✅ File uploaded: {uploaded_file.name}")
        st.info(f"📊 Dimensions: {df.shape[0]} rows × {df.shape[1]} columns")
        try:
            calculator = DebugPSICalculator(
                codes_source_path="PSI_Code_Sets.json",
                psi_definitions_path="PSI_02_19_Compiled_Cleaned.json"
            )
        except Exception as e:
            st.error(f"❌ Failed to initialize PSI Calculator: {e}")
            st.stop()
        col1, col2 = st.columns([3, 1])
        with col1:
            analyze_button = st.button("🚀 Analyze PSI", type="primary")
        with col2:
            debug_mode = st.checkbox("🔍 Global Debug Mode (ALL Encounters/PSIs)", help="Enable forensic bug tracing for every result (may slow UI for very large files).")
        if analyze_button:
            with st.spinner("🔬 Running PSI analysis..."):
                results_df, error_df = run_psi_analysis(df, calculator, debug_mode)
                st.session_state.results_df = results_df
                st.session_state.error_df = error_df
                st.session_state.analysis_complete = True
            st.success(f"✅ Analysis completed! Generated {len(results_df)} results.")
        if st.session_state.analysis_complete and st.session_state.results_df is not None:
            st.subheader("📊 Dashboard")
            display_dashboard(st.session_state.results_df)
            st.subheader("📋 Results")
            view_mode = st.radio(
                "Select View Mode:",
                ["All Results (Complete Analysis)", "Inclusions Only (Flagged Events)"],
                horizontal=True
            )
            if view_mode == "Inclusions Only (Flagged Events)":
                inclusions_df = st.session_state.results_df[st.session_state.results_df["Status"] == "Inclusion"]
                if not inclusions_df.empty:
                    st.info(f"📍 Showing {len(inclusions_df)} flagged safety events from {st.session_state.results_df['EncounterID'].nunique()} encounters")
                    display_results_table(inclusions_df, debug_mode)
                else:
                    st.success("🎉 No PSI inclusions found - All encounters passed safety checks!")
            else:
                display_results_table(st.session_state.results_df, debug_mode)
            if st.session_state.error_df is not None and not st.session_state.error_df.empty:
                st.subheader("⚠️ Error Log")
                st.error(f"Found {len(st.session_state.error_df)} errors during analysis:")
                st.dataframe(st.session_state.error_df, use_container_width=True)
                error_csv = st.session_state.error_df.to_csv(index=False, encoding="utf-8-sig")
                st.download_button("⬇️ Download Error Log", data=error_csv, file_name="PSI_Errors.csv")
    except Exception as e:
        st.error(f"❌ Unexpected error: {e}")
else:
    st.info("Upload a file to begin PSI analysis")
    if st.session_state.analysis_complete:
        st.session_state.results_df = None
        st.session_state.error_df = None
        st.session_state.analysis_complete = False
        st.session_state.debug_reports = {}
