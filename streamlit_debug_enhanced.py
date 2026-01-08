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
    def debug_forensic_report(self, row, psi_code, status, rationale, match_details):
        """
        Generates a deep forensic debug report for any encounter and PSI, 
        including a checklist of matched conditions.
        """
        report_lines = []
        enc_id = row.get('EncounterID', 'UNKNOWN')
        age = row.get('AGE')
        mdc = row.get('MDC')
        drg = row.get('MS-DRG')
        pdx = row.get('Pdx')
        
        report_lines.append(f"### 🔍 Forensic Analysis: {psi_code}")
        report_lines.append(f"**Encounter ID:** {enc_id}")
        report_lines.append(f"**Final Status:** {status}")
        report_lines.append(f"**Summary Rationale:** {rationale}")
        
        report_lines.append("\n---")
        report_lines.append("### ✅ Checklist of Matched Conditions")
        
        if match_details and isinstance(match_details, dict):
            # Display inclusion/exclusion details as a list
            for category, details in match_details.items():
                report_lines.append(f"**{category}:**")
                if isinstance(details, list):
                    for item in details:
                        report_lines.append(f"- {item}")
                else:
                    report_lines.append(f"- {details}")
        else:
            report_lines.append("_No specific checklist items matched._")

        report_lines.append("\n---")
        report_lines.append("### 📊 Metadata Reference")
        report_lines.append(f"- **AGE:** {age}")
        report_lines.append(f"- **MDC:** {mdc}")
        report_lines.append(f"- **MS-DRG:** {drg}")
        report_lines.append(f"- **Principal DX:** `{pdx}`")
        
        if hasattr(self, '_get_all_diagnoses'):
            diagnoses = self._get_all_diagnoses(row)
            report_lines.append(f"- **All DX Codes:** `{', '.join(map(str, diagnoses))}`")
        
        return "\n".join(report_lines)

    def evaluate_psi(self, row: pd.Series, psi_code: str):
        # Run standard exclusion and logic
        # We assume the base class evaluate_psi returns (status, rationale, code, details)
        status, rationale, _, details = super().evaluate_psi(row, psi_code)
        
        # Save forensic report for this row/PSI
        key = (row.get('EncounterID'), psi_code)
        report = self.debug_forensic_report(row, psi_code, status, rationale, details)
        st.session_state.debug_reports[key] = report
        
        return status, rationale, psi_code, details

def run_psi_analysis(df, calculator):
    results = []
    errors = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    st.session_state.debug_reports = {} 
    
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
    col1.metric("Total Evaluations", total)
    col2.metric("Inclusions (Flags)", inclusions)
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
        
    st.write(f"Showing {len(filtered_df)} results")
    st.dataframe(filtered_df, use_container_width=True)
    
    # Forensic Checklist Expanders
    if not filtered_df.empty:
        st.write("### 📝 Forensic Checklists")
        st.caption("Click to see which specific rules/labels matched for each outcome.")
        for _, row in filtered_df.iterrows():
            key = (row["EncounterID"], row["PSI"])
            report = st.session_state.debug_reports.get(key)
            status_color = "🔴" if row["Status"] == "Inclusion" else "🟡" if row["Status"] == "Exclusion" else "⚪"
            
            with st.expander(f"{status_color} {row['PSI']} | {row['EncounterID']} | {row['Status']}"):
                if report:
                    st.markdown(report)
                else:
                    st.info("No detailed checklist available for this entry.")

    csv_data = filtered_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("⬇️ Download Results as CSV", data=csv_data, file_name="PSI_Results.csv")

# --- UI Layout ---

uploaded_file = st.file_uploader("📂 Upload Patient Data (Excel/CSV)", type=["xlsx", "xls", "csv"])

if uploaded_file:
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        
        df.columns = df.columns.str.strip()
        st.success(f"✅ Loaded {len(df)} encounters.")
        
        try:
            calculator = DebugPSICalculator(
                codes_source_path="PSI_Code_Sets.json",
                psi_definitions_path="PSI_02_19_Compiled_Cleaned.json"
            )
        except Exception as e:
            st.error(f"❌ Initialization Error: {e}")
            st.stop()
            
        if st.button("🚀 Run Analysis", type="primary"):
            with st.spinner("Processing AHRQ PSI Logic..."):
                results_df, error_df = run_psi_analysis(df, calculator)
                st.session_state.results_df = results_df
                st.session_state.error_df = error_df
                st.session_state.analysis_complete = True
            st.rerun()

        if st.session_state.analysis_complete:
            st.divider()
            display_dashboard(st.session_state.results_df)
            
            tab1, tab2 = st.tabs(["📋 Analysis Results", "⚠️ Errors"])
            
            with tab1:
                display_results_table(st.session_state.results_df)
                
            with tab2:
                if st.session_state.error_df is not None and not st.session_state.error_df.empty:
                    st.dataframe(st.session_state.error_df, use_container_width=True)
                else:
                    st.success("No processing errors detected.")

    except Exception as e:
        st.error(f"❌ Error: {e}")
else:
    st.info("Upload your encounter data to begin forensic analysis.")
