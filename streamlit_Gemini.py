from PSI_02_19_Patched_POA_All import PSICalculator
import streamlit as st
import pandas as pd
import json
import requests # Import the requests library for API calls

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

def get_gemini_explanation(prompt: str, row: pd.Series, psi_code: str, calculator: PSICalculator) -> str:
    """
    Fetches an explanation from the Gemini API, incorporating content-aware details
    from PSI definitions and code sets.
    """
    chat_history = []
    
    # Build content-aware context for the prompt
    context_lines = []
    context_lines.append(f"\n--- Context for {psi_code} Analysis ---")
    
    psi_def = calculator.psi_definitions.get(psi_code, {})
    population_type = psi_def.get('indicator', {}).get('population_type', 'N/A')
    context_lines.append(f"PSI Type: {psi_def.get('indicator', {}).get('name', 'N/A')}")
    context_lines.append(f"Population Type: {population_type}")

    # Add relevant diagnoses and procedures from the patient's row that match known code sets
    all_diagnoses = calculator._get_all_diagnoses(row)
    all_procedures = calculator._get_all_procedures(row)

    relevant_code_info = []

    # Iterate through PSI definition to find relevant code sets
    # This part can be made more sophisticated to parse all rules, but for simplicity,
    # we'll look for common patterns or hardcode for known PSIs if structure is complex.
    
    # Example for PSI_04 (as it was the last point of discussion)
    if psi_code == 'PSI_04':
        context_lines.append("\nPSI_04 Specific Rules (simplified):")
        context_lines.append("- Denominator requires surgical DRG (SURGI2R), OR procedure (ORPROC), and specific age/obstetric criteria.")
        context_lines.append("- Must have one of 5 serious complications (Shock, Sepsis, Pneumonia, GI Hemorrhage, DVT/PE) based on specific secondary diagnoses/procedures.")
        context_lines.append("- Exclusions apply for principal diagnoses matching complication codes, transfers, hospice, newborn MDC15.")

        # Check for relevant codes in patient data against PSI_04 rules
        relevant_psi04_code_sets = set()
        for stratum_name, rules in calculator.psi04_rules.items():
            for rule_type, rule_values in rules.get('inclusion', {}).items():
                if rule_type == 'secondary_dx':
                    relevant_psi04_code_sets.update(rule_values)
                elif rule_type == 'procedure_after_or':
                    relevant_psi04_code_sets.add(rule_values['code_set'])
            for rule_type, rule_values in rules.get('exclusions', {}).items():
                if rule_type in ['principal_dx', 'any_dx', 'any_proc']:
                    relevant_psi04_code_sets.update(rule_values)
                elif rule_type == 'secondary_dx_combined':
                    relevant_psi04_code_sets.add(rule_values['dx_code_set_1'])
                    relevant_psi04_code_sets.add(rule_values['principal_dx_code_set_2'])
        
        # Add general PSI_04 relevant sets
        relevant_psi04_code_sets.add('SURGI2R')
        relevant_psi04_code_sets.add('ORPROC')
        relevant_psi04_code_sets.add('MDC14PRINDX')
        relevant_psi04_code_sets.add('MDC15PRINDX')
        relevant_psi04_code_sets.add('DISP') # For discharge disposition
        relevant_psi04_code_sets.add('POINTOFORIGINUB04') # For admission source

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']
            for cs_name in relevant_psi04_code_sets:
                if dx_code in calculator.code_sets.get(cs_name, set()):
                    relevant_code_info.append(f"- Diagnosis '{dx_code}' (POA: {poa_status}) is in code set '{cs_name}' (e.g., {list(calculator.code_sets.get(cs_name, set()))[:3]}...)")
                    break # Found a match, move to next dx_entry

        for proc_entry in all_procedures:
            proc_code = proc_entry['code']
            for cs_name in relevant_psi04_code_sets:
                if proc_code in calculator.code_sets.get(cs_name, set()):
                    relevant_code_info.append(f"- Procedure '{proc_code}' is in code set '{cs_name}' (e.g., {list(calculator.code_sets.get(cs_name, set()))[:3]}...)")
                    break # Found a match, move to next proc_entry
    
    # Add a generic fallback for other PSIs if specific logic isn't implemented yet
    else:
        context_lines.append("\nGeneral Code Set Matches:")
        # For other PSIs, iterate through all patient diagnoses/procedures
        # and see if they match any known code sets.
        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']
            for cs_name, codes in calculator.code_sets.items():
                if dx_code in codes:
                    relevant_code_info.append(f"- Diagnosis '{dx_code}' (POA: {poa_status}) is in code set '{cs_name}' (e.g., {list(codes)[:3]}...)")
                    break
        for proc_entry in all_procedures:
            proc_code = proc_entry['code']
            for cs_name, codes in calculator.code_sets.items():
                if proc_code in codes:
                    relevant_code_info.append(f"- Procedure '{proc_code}' is in code set '{cs_name}' (e.g., {list(codes)[:3]}...)")
                    break

    if relevant_code_info:
        context_lines.append("Relevant patient data found in the following code sets:")
        context_lines.extend(relevant_code_info)
    else:
        context_lines.append("No specific code set matches found for this patient data in the context of this PSI.")


    full_prompt = f"{prompt}\n\n{' '.join(context_lines)}\n\nBased on the provided PSI definition, the patient's data, and the relevant code sets, provide a detailed and clear explanation for why this encounter received the '{row['Status']}' status for {psi_code}. Focus on how specific data points (diagnoses, procedures, age, DRG, POA status, etc.) interact with the PSI's rules and the provided code sets."

    chat_history.append({"role": "user", "parts": [{"text": full_prompt}]})
    payload = {"contents": chat_history}

    try:
        apiKey = st.secrets["gemini_api_key"]
    except KeyError:
        return "Error: Gemini API key not found in Streamlit secrets. Please configure it."
    
    apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={apiKey}"
    
    try:
        response = requests.post(apiUrl, headers={'Content-Type': 'application/json'}, data=json.dumps(payload))
        response.raise_for_status() # Raise an exception for HTTP errors
        result = response.json()

        if result.get('candidates') and len(result['candidates']) > 0 and \
           result['candidates'][0].get('content') and result['candidates'][0]['content'].get('parts') and \
           len(result['candidates'][0]['content']['parts']) > 0:
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            return "No explanation generated."
    except requests.exceptions.RequestException as e:
        return f"Error making API request: {e}"
    except json.JSONDecodeError:
        return "Error decoding API response."
    except Exception as e:
        return f"An unexpected error occurred: {e}"

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
                
                # Add Gemini explanation button for each row in debug mode
                # Use a unique key for each button
                if st.button(f"✨ Explain with Gemini (Encounter: {row['EncounterID']}, PSI: {row['PSI']})", key=f"gemini_explain_{i}_{row['EncounterID']}_{row['PSI']}"):
                    with st.spinner("Generating explanation with Gemini..."):
                        # Pass row and calculator to get_gemini_explanation
                        prompt_base = f"Explain the PSI result for EncounterID: {row['EncounterID']}, PSI: {row['PSI']}. Status: {row['Status']}. Rationale: {row['Rationale']}. Here is the full debug report:\n\n{report}"
                        explanation_text = get_gemini_explanation(prompt_base, row, row['PSI'], calculator)
                        
                        # Store explanation in session state to avoid re-generating on rerun
                        explanation_key = f"gemini_explanation_{key}"
                        st.session_state[explanation_key] = explanation_text # Store the generated text
                        
                        if explanation_text:
                            st.markdown("---")
                            st.subheader("Gemini Explanation:")
                            st.write(explanation_text)
                        else:
                            st.error("Could not generate explanation.")


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
