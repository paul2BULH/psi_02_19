from PSI_02_19_Patched_POA_All import PSICalculator
import streamlit as st
import pandas as pd
import json
import requests # Import the requests library for API calls

st.set_page_config(page_title="PSI Analyzer", layout="wide")
st.title("🧬 Patient Safety Indicator (PSI) Analyzer")

# Initialize session state variables if they don't exist
if 'results_df' not in st.session_state:
    st.session_state.results_df = None
if 'error_df' not in st.session_state:
    st.session_state.error_df = None
if 'analysis_complete' not in st.session_state:
    st.session_state.analysis_complete = False
if 'debug_reports' not in st.session_state:
    st.session_state.debug_reports = {}
if 'gemini_explanations' not in st.session_state:
    st.session_state.gemini_explanations = {}

PSI_CODES = [f"PSI_{i:02}" for i in range(2, 20) if i != 16]

# Define required columns for the input DataFrame
REQUIRED_COLUMNS = ["EncounterID", "AGE", "MDC", "MS-DRG", "Pdx"]

class DebugPSICalculator:
    """
    A wrapper class for PSICalculator that adds forensic debug reporting capabilities.
    This decouples the debugging logic from the core PSI evaluation.
    """
    def __init__(self, codes_source_path, psi_definitions_path):
        self.psi_calculator = PSICalculator(codes_source_path, psi_definitions_path)
        self.codes_source_path = codes_source_path
        self.psi_definitions_path = psi_definitions_path

    def evaluate_psi(self, row: pd.Series, psi_code: str):
        """
        Evaluates PSI for a given row and generates a debug report if debug mode is active.
        """
        # Call the actual PSI calculation from the wrapped calculator
        status, rationale, _, _ = self.psi_calculator.evaluate_psi(row, psi_code)

        # Generate and store the forensic report
        enc_id = row.get('EncounterID', 'UNKNOWN')
        key = (enc_id, psi_code)
        report = self._generate_forensic_report(row, psi_code, status, rationale)
        st.session_state.debug_reports[key] = report
        
        return status, rationale, psi_code, {}

    def _generate_forensic_report(self, row, psi_code, status, rationale):
        """
        Generates a deep forensic debug report for any encounter and PSI.
        Accesses internal data/methods of the wrapped psi_calculator for detailed info.
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

        # Accessing private methods of the wrapped calculator for detailed diagnostics
        if hasattr(self.psi_calculator, '_get_all_diagnoses'):
            diagnoses = self.psi_calculator._get_all_diagnoses(row)
            report_lines.append(f"All Diagnoses: {diagnoses}")
        if hasattr(self.psi_calculator, '_get_all_procedures'):
            procedures = self.psi_calculator._get_all_procedures(row)
            report_lines.append(f"All Procedures: {procedures}")

        # Obstetric path (MDC 14) specific debug
        try:
            mdc14prindx = self.psi_calculator.code_sets.get('MDC14PRINDX', set())
            if pd.notna(mdc) and str(mdc) == "14":
                pdx_str = str(pdx)
                upper_match = pdx_str.strip().upper() in {c.strip().upper() for c in mdc14prindx}
                report_lines.append(f"(Obstetric) MDC==14, Pdx in MDC14PRINDX: {upper_match}")
                report_lines.append(f"Principal DX (normalized): '{pdx_str.strip().upper()}'")
                report_lines.append(f"Sample MDC14PRINDX codes (normalized): {list(sorted({c.strip().upper() for c in mdc14prindx}))[:10]}")
                report_lines.append(f"O10019 in set: {'O10019' in {c.strip().upper() for c in mdc14prindx}}")
        except Exception as e:
            report_lines.append(f"[Obstetric Path Debug Failed: {e}]")

        # DRG/age logic for surgical/medical specific debug
        try:
            surg_set = self.psi_calculator.code_sets.get('SURGI2R', set())
            med_set = self.psi_calculator.code_sets.get('MEDIC2R', set())
            drg_val = str(drg).strip().upper() if pd.notna(drg) else ''
            drg_surg = drg_val in {c.strip().upper() for c in surg_set}
            drg_med = drg_val in {c.strip().upper() for c in med_set}
            report_lines.append(f"Surgical DRG match: {drg_surg}")
            report_lines.append(f"Medical DRG match: {drg_med}")
        except Exception as e:
            report_lines.append(f"[DRG Path Debug Failed: {e}]")

        report_lines.append("=" * 60)
        return "\n".join(report_lines)

def run_psi_analysis(df, calculator, debug_mode=False):
    """
    Runs the PSI analysis on the DataFrame and collects results and errors.
    Includes enhanced progress reporting.
    """
    results = []
    errors = []
    
    # Clear previous debug reports and Gemini explanations
    st.session_state.debug_reports = {}  
    st.session_state.gemini_explanations = {}

    total_encounters = len(df)
    total_evaluations = total_encounters * len(PSI_CODES)
    current_evaluation = 0

    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, row in df.iterrows():
        enc_id = row.get("EncounterID", f"Row{idx+1}")
        status_text.text(f"Processing Encounter {idx+1}/{total_encounters}: {enc_id}...")
        
        for psi_code in PSI_CODES:
            try:
                # Use the evaluate_psi from the (Debug)PSICalculator instance
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
    """Displays a summary dashboard of PSI results."""
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

def get_gemini_explanation(prompt: str) -> str:
    """Fetches an explanation from the Gemini API."""
    chat_history = []
    chat_history.push({"role": "user", "parts": [{"text": prompt}]})
    payload = {"contents": chat_history}

    try:
        # Retrieve API key from Streamlit secrets
        # Ensure you have a [secrets] section in .streamlit/secrets.toml
        # with gemini_api_key = "YOUR_API_KEY_HERE"
        apiKey = st.secrets["gemini_api_key"]
    except KeyError:
        return "Error: Gemini API key not found in Streamlit secrets. Please configure it."
    
    apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={apiKey}"
    
    try:
        response = requests.post(apiUrl, headers={'Content-Type': 'application/json'}, data=json.dumps(payload))
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        result = response.json()

        if result.get('candidates') and len(result['candidates']) > 0 and \
           result['candidates'][0].get('content') and result['candidates'][0]['content'].get('parts') and \
           len(result['candidates'][0]['content']['parts']) > 0:
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            return "No explanation generated or unexpected API response structure."
    except requests.exceptions.RequestException as e:
        return f"Error making API request: {e}"
    except json.JSONDecodeError:
        return "Error decoding API response (response was not valid JSON)."
    except Exception as e:
        return f"An unexpected error occurred during Gemini API call: {e}"

def display_results_table(results_df, debug_mode=False):
    """
    Displays the results table with filtering, download, and optional debug/Gemini explanation features.
    """
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
    
    # Show forensic debug for each row if debug_mode is enabled
    if debug_mode and not filtered_df.empty:
        st.subheader("🔬 Debug Reports & Gemini Explanations")
        for i, row in filtered_df.iterrows():
            enc_id = row['EncounterID']
            psi_code = row['PSI']
            status = row['Status']
            rationale = row['Rationale']
            
            # Key for debug report and Gemini explanation
            report_key = (enc_id, psi_code)
            gemini_explanation_key = f"gemini_explanation_{enc_id}_{psi_code}"

            report = st.session_state.debug_reports.get(report_key)
            
            with st.expander(f"Encounter: {enc_id} | PSI: {psi_code} | Status: {status}"):
                st.text(report if report else "No debug report available for this row.")
                
                # Add Gemini explanation button for each row in debug mode
                # Use a unique key for each button to prevent issues with Streamlit re-runs
                if st.button(f"✨ Explain with Gemini", key=f"gemini_explain_btn_{i}_{enc_id}_{psi_code}"):
                    with st.spinner(f"Generating explanation for {enc_id} - {psi_code} with Gemini..."):
                        prompt = f"Explain the PSI result for EncounterID: {enc_id}, PSI: {psi_code}. Status: {status}. Rationale: {rationale}. Here is the full debug report:\n\n{report}"
                        
                        # Only call API if explanation is not already in session state
                        if gemini_explanation_key not in st.session_state.gemini_explanations:
                            st.session_state.gemini_explanations[gemini_explanation_key] = get_gemini_explanation(prompt)
                        
                        explanation = st.session_state.gemini_explanations.get(gemini_explanation_key)
                        
                        if explanation:
                            st.markdown("---")
                            st.subheader("Gemini Explanation:")
                            st.write(explanation)
                        else:
                            st.error("Could not generate explanation.")
                # Display cached explanation if available and button wasn't just clicked (to avoid flicker)
                elif gemini_explanation_key in st.session_state.gemini_explanations:
                    explanation = st.session_state.gemini_explanations.get(gemini_explanation_key)
                    if explanation:
                        st.markdown("---")
                        st.subheader("Gemini Explanation (Cached):")
                        st.write(explanation)

    csv_data = filtered_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("⬇️ Download Filtered Results", data=csv_data, file_name="PSI_Results.csv")

# --- Main Application Logic ---
uploaded_file = st.file_uploader("📂 Upload Excel or CSV File", type=["xlsx", "xls", "csv"])

if uploaded_file:
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        
        # Strip whitespace from column names for consistent access
        df.columns = df.columns.str.strip()

        st.success(f"✅ File uploaded: {uploaded_file.name}")
        st.info(f"📊 Dimensions: {df.shape[0]} rows × {df.shape[1]} columns")

        # Validate required columns
        missing_columns = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing_columns:
            st.error(f"❌ Missing required columns in the uploaded file: {', '.join(missing_columns)}. Please ensure your file contains these columns.")
            st.stop() # Stop execution if essential columns are missing
        
        try:
            # Initialize the DebugPSICalculator which wraps the PSICalculator
            calculator = DebugPSICalculator(
                codes_source_path="PSI_Code_Sets.json",
                psi_definitions_path="PSI_02_19_Compiled_Cleaned.json"
            )
            st.info(f"Using PSI Code Sets from: `{calculator.codes_source_path}`")
            st.info(f"Using PSI Definitions from: `{calculator.psi_definitions_path}`")

        except Exception as e:
            st.error(f"❌ Failed to initialize PSI Calculator. Ensure 'PSI_Code_Sets.json' and 'PSI_02_19_Compiled_Cleaned.json' are available: {e}")
            st.stop()
        
        col1, col2 = st.columns([3, 1])
        with col1:
            analyze_button = st.button("🚀 Analyze PSI", type="primary")
        with col2:
            debug_mode = st.checkbox("🔍 Global Debug Mode (ALL Encounters/PSIs)", help="Enable forensic bug tracing for every result (may slow UI for very large files). Debug reports are stored in memory.")
        
        if analyze_button:
            with st.spinner("🔬 Running PSI analysis... This may take a while for large files."):
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
        st.error(f"❌ An unexpected error occurred during file processing or analysis: {e}")
else:
    st.info("Upload a file to begin PSI analysis")
    # Reset state when no file is uploaded, effectively clearing previous analysis
    if st.session_state.analysis_complete:
        st.session_state.results_df = None
        st.session_state.error_df = None
        st.session_state.analysis_complete = False
        st.session_state.debug_reports = {}
        st.session_state.gemini_explanations = {}

