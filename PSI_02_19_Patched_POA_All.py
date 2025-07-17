import pandas as pd
import json
from datetime import datetime, timedelta, time
from typing import Dict, Any, List, Optional, Tuple, Set

class PSICalculator:
    """
    A class to calculate Patient Safety Indicators (PSIs) based on provided
    patient encounter data and a comprehensive set of appendix codes.

    This framework handles:
    1. Loading all required code reference sets from a specified appendix file.
    2. Parsing and validating patient encounter data.
    3. Applying base exclusion logic common to many PSIs.
    4. Returning structured results for each PSI evaluation.
    """

    def __init__(self, codes_source_path: str = 'PSI_Code_Sets.json', psi_definitions_path: str = 'PSI_02_19_Compiled_Cleaned.json'):
        """
        Initializes the PSICalculator with code sets and PSI definitions.

        Args:
            codes_source_path (str): Path to the JSON file containing code sets.
            psi_definitions_path (str): Path to the JSON file containing PSI definitions.
        """
        self.code_sets = self._load_code_sets(codes_source_path)
        self.psi_definitions = self._load_psi_definitions(psi_definitions_path)

        # Pdx is the principal diagnosis, DX1-DX25 are secondary diagnoses
        self.dx_cols = ['Pdx'] + [f"DX{i}" for i in range(1, 26)] # Pdx, DX1 to DX25
        # POA1 corresponds to Pdx, POA2 to DX1, ..., POA26 to DX25.
        self.poa_cols = [f"POA{i}" for i in range(1, 27)] # POA1 to POA26

        self.proc_cols = [f"Proc{i}" for i in range(1, 11)] # Proc1 to Proc10
        self.proc_date_cols = [f"Proc{i}_Date" for i in range(1, 11)] # Proc1_Date to Proc10_Date
        self.proc_time_cols = [f"Proc{i}_Time" for i in range(1, 11)] # Proc1_Time to Proc10_Time

        # PSI_03 Specific Anatomic Site Mappings
        self.anatomic_site_map: Dict[str, str] = {
            'PIRELBOWD': 'DTIRELBOEXD',
            'PILELBOWD': 'DTILELBOEXD',
            'PIRUPBACKD': 'DTIRUPBACEXD',
            'PILUPBACKD': 'DTILUPBACEXD',
            'PIRLOBACKD': 'DTIRLOBACEXD',
            'PILLOBACKD': 'DTILLOBACEXD',
            'PISACRALD': 'DTISACRAEXD',
            'PIRHIPD': 'DTIRHIPEXD',
            'PILHIPD': 'DTILHIPEXD',
            'PIRBUTTD': 'DTIRBUTEXD',
            'PILBUTTD': 'DTILBUTEXD',
            'PICONTIGBBHD': 'DTICONTBBHEXD',
            'PIRANKLED': 'DTIRANKLEXD',
            'PILANKLED': 'DTILANKLEXD',
            'PIRHEELD': 'DTIRHEELEXD',
            'PILHEELD': 'DTILHEELEXD',
            'PIHEADD': 'DTIHEADEXD',
            'PIOTHERD': 'DTIOTHEREXD',
        }
        # These are the *names* of the code sets for unspecified PUs, not the codes themselves
        self.unspecified_pu_code_set_names: Set[str] = {
            'PINELBOWD', 'PINBACKD', 'PINHIPD', 'PINBUTTD',
            'PINANKLED', 'PINHEELD', 'PIUNSPECD'
        }

        # Store the keys of anatomic_site_map for later lookup of code set names
        self.all_specific_pu_codes_keys_from_map: Set[str] = set(self.anatomic_site_map.keys())

        # Populate self.all_specific_pu_codes with actual ICD-10 codes for specific pressure ulcers
        # This is corrected to get the actual codes from the appendix.
        self.all_specific_pu_codes: Set[str] = set()
        for code_set_name in self.all_specific_pu_codes_keys_from_map:
            self.all_specific_pu_codes.update(self.code_sets.get(code_set_name, set()))


        # PI~EXD* codes for principal/POA=Y secondary exclusion (union of PI and DTI exclusions)
        # This set is specifically for the PSI_03 Denominator Exclusion:
        # "with a principal ICD-10-CM diagnosis code for site-specific pressure ulcer stage 3 or 4 (or unstageable)
        # or deep tissue injury at the same anatomic site ( PI~EXD *)"
        self.pi_exd_codes_for_principal_exclusion: Set[str] = set()
        # Add all specific PI~D* codes (e.g., L8943)
        self.pi_exd_codes_for_principal_exclusion.update(self.all_specific_pu_codes)
        # Add all DTI~EXD* codes (e.g., from DTIRELBOEXD)
        for dti_code_set_name in self.anatomic_site_map.values():
            self.pi_exd_codes_for_principal_exclusion.update(self.code_sets.get(dti_code_set_name, set()))
        # Add all unspecified PU codes (e.g., PIUNSPECD)
        for unspecified_set_name in self.unspecified_pu_code_set_names:
             self.pi_exd_codes_for_principal_exclusion.update(self.code_sets.get(unspecified_set_name, set()))


        # PSI_15 Specific Organ System Mappings
        self.organ_system_mappings: Dict[str, Dict[str, str]] = {
            'spleen': {'dx_codes': 'SPLEEN15D', 'proc_codes': 'SPLEEN15P'},
            'adrenal': {'dx_codes': 'ADRENAL15D', 'proc_codes': 'ADRENAL15P'},
            'vessel': {'dx_codes': 'VESSEL15D', 'proc_codes': 'VESSEL15P'},
            'diaphragm': {'dx_codes': 'DIAPHR15D', 'proc_codes': 'DIAPHR15P'},
            'gastrointestinal': {'dx_codes': 'GI15D', 'proc_codes': 'GI15P'},
            'genitourinary': {'dx_codes': 'GU15D', 'proc_codes': 'GU15P'},
        }
        # Consolidate all PSI_15 injury DX codes for easier lookup
        self.all_psi15_injury_dx_codes: Set[str] = set()
        for system_map in self.organ_system_mappings.values():
            # Populate with actual codes from code_sets, not just the code set names
            self.all_psi15_injury_dx_codes.update(self.code_sets.get(system_map['dx_codes'], set()))

        # PSI_04 Strata Definitions (for internal use) - Ordered by priority as per JSON (1 is highest)
        self.psi04_strata_priority = [
            'STRATUM_SHOCK',
            'STRATUM_SEPSIS',
            'STRATUM_PNEUMONIA',
            'STRATUM_GI_HEMORRHAGE',
            'STRATUM_DVT_PE'
        ]

        # Structured PSI_04 rules for better readability and maintainability
        self.psi04_rules = {
            'STRATUM_SHOCK': {
                'inclusion': {
                    'secondary_dx_not_poa': ['FTR5DX'],
                    'procedure_after_or': {'code_set': 'FTR5PR', 'min_days_after_or': 0, 'inclusive_min': True}
                },
                'exclusions': {
                    'principal_dx': ['FTR5DX', 'TRAUMID', 'HEMORID', 'GASTRID', 'FTR5EX'],
                    'secondary_dx_combined': [
                        {'dx_code_set_1': 'FTR6GV', 'principal_dx_code_set_2': 'FTR6QD'}
                    ],
                    'mdc': [4, 5]
                }
            },
            'STRATUM_SEPSIS': {
                'inclusion': {
                    'secondary_dx_not_poa': ['FTR4DX']
                },
                'exclusions': {
                    'principal_dx': ['FTR4DX', 'INFECID']
                }
            },
            'STRATUM_PNEUMONIA': {
                'inclusion': {
                    'secondary_dx_not_poa': ['FTR3DX']
                },
                'exclusions': {
                    'principal_dx': ['FTR3DX', 'FTR3EXA'],
                    'any_dx': ['FTR3EXB'],
                    'any_proc': ['LUNGCIP'],
                    'mdc': [4]
                }
            },
            'STRATUM_GI_HEMORRHAGE': {
                'inclusion': {
                    'secondary_dx_not_poa': ['FTR6DX']
                },
                'exclusions': {
                    'principal_dx': ['FTR6DX', 'TRAUMID', 'ALCHLSM', 'FTR6EX'],
                    'secondary_dx_combined': [
                        {'dx_code_set_1': 'FTR6GV', 'principal_dx_code_set_2': 'FTR6QD'}
                    ],
                    'mdc': [6, 7]
                }
            },
            'STRATUM_DVT_PE': {
                'inclusion': {
                    'secondary_dx_not_poa': ['FTR2DXB']
                },
                'exclusions': {
                    'principal_dx': ['FTR2DXB', 'OBEMBOL']
                }
            }
        }


    def _load_code_sets(self, codes_source_path: str) -> Dict[str, Set[str]]:
        """
        Loads code reference sets from a JSON file.
        The JSON file is expected to have a structure like:
        {"CODE_SET_NAME_1": ["code1", "code2", ...], "CODE_SET_NAME_2": ["codeA", "codeB", ...]}

        Args:
            codes_source_path (str): Path to the JSON file containing code sets.

        Returns:
            dict: A dictionary where keys are code set names and values are sets of codes.
        """
        code_sets: Dict[str, Set[str]] = {}
        try:
            with open(codes_source_path, 'r') as f:
                data = json.load(f)
                for code_set_name, codes_list in data.items():
                    if not isinstance(codes_list, list):
                        print(f"Warning: Code set '{code_set_name}' in '{codes_source_path}' is not a list. Skipping.")
                        continue
                    code_sets[code_set_name] = set(codes_list)
                    if not codes_list:
                        print(f"Warning: Code set '{code_set_name}' is empty. Ensure all required code sets have values.")
            print(f"Successfully loaded {len(code_sets)} code sets from {codes_source_path}.")
        except FileNotFoundError:
            print(f"Error: Code sets file not found at {codes_source_path}. Initializing with empty code sets.")
        except json.JSONDecodeError:
            print(f"Error: Could not decode JSON from {codes_source_path}. Check file format.")
        except Exception as e:
            print(f"Error loading code sets from {codes_source_path}: {e}")
        return code_sets

    def _load_psi_definitions(self, psi_definitions_path: str) -> Dict[str, Any]:
        """
        Loads PSI definitions from a JSON file.

        Args:
            psi_definitions_path (str): Path to the JSON file containing PSI definitions.

        Returns:
            dict: A dictionary containing PSI definitions.
        """
        try:
            with open(psi_definitions_path, 'r') as f:
                psi_data: Dict[str, Any] = json.load(f)
                return psi_data.get('data', {})
        except FileNotFoundError:
            print(f"Error: PSI definitions file not found at {psi_definitions_path}")
            return {}
        except json.JSONDecodeError:
            print(f"Error: Could not decode JSON from {psi_definitions_path}")
            return {}
        except Exception as e:
            print(f"Error loading PSI definitions: {e}")
            return {}

    def _parse_date_string(self, date_str, time_str=None, encounter_id=None):
        from datetime import datetime, timedelta
        import pandas as pd

        if pd.isna(date_str):
            return pd.NaT

        try:
            # Clean the date string (remove time if embedded)
            clean_date_str = str(date_str).strip().split()[0]
            dt_obj = datetime.strptime(clean_date_str, '%Y-%m-%d')

            # If time_str exists, try adding it
            if time_str and not pd.isna(time_str):
                clean_time_str = str(time_str).strip()
                if ":" in clean_time_str:
                    # Format: HH:MM or HH:MM:SS
                    try:
                        time_parts = clean_time_str.split(":")
                        hours = int(time_parts[0])
                        minutes = int(time_parts[1])
                        dt_obj = dt_obj + timedelta(hours=hours, minutes=minutes)
                    except:
                        pass
                elif len(clean_time_str) == 4 and clean_time_str.isdigit():
                    # Format: 0830
                    hours = int(clean_time_str[:2])
                    minutes = int(clean_time_str[2:])
                    dt_obj = dt_obj + timedelta(hours=hours, minutes=minutes)
                # Else: fallback, skip bad time

            return dt_obj

        except Exception as e:
            if encounter_id:
                print(f"Warning: Could not parse date/time for EncounterID {encounter_id}: '{date_str}' '{time_str}' - {e}")
            return pd.NaT

    def _get_admission_discharge_dates(self, row: pd.Series) -> Tuple[pd.Timestamp, pd.Timestamp]:
        """
        Extracts and parses Admission_Date and Discharge_Date from a row.
        """
        admission_date: pd.Timestamp = self._parse_date_string(row.get('Admission_Date'), encounter_id=row.get('EncounterID'))
        discharge_date: pd.Timestamp = self._parse_date_string(row.get('Discharge_Date'), encounter_id=row.get('EncounterID'))
        return admission_date, discharge_date

    def _get_all_diagnoses(self, row: pd.Series) -> List[Dict[str, Optional[str]]]:
        """
        Extracts all diagnosis codes and their POA statuses from a row,
        correctly mapping Pdx to POA1, DX1 to POA2, etc.
        Normalizes POA status: 'E' (Exempt) is treated as 'Y' (Present on Admission).
        """
        diagnoses: List[Dict[str, Optional[str]]] = []

        # Helper to normalize POA status
        def normalize_poa(poa_raw: Optional[str]) -> Optional[str]:
            if pd.isna(poa_raw):
                return None
            poa_str = str(poa_raw).strip().upper()
            if poa_str == 'E': # Treat 'E' (Exempt) as 'Y' (Present on Admission)
                return 'Y'
            return poa_str

        # Principal diagnosis: Pdx + POA1
        pdx_code = row.get('Pdx')
        pdx_poa_raw = row.get('POA1')
        if pd.notna(pdx_code):
            diagnoses.append({'code': str(pdx_code), 'poa': normalize_poa(pdx_poa_raw)})

        # Secondary diagnoses: DX1-DX25 + POA2-POA26
        for i in range(1, 26):  # DX1 to DX25
            dx_code = row.get(f'DX{i}')
            poa_status_col_name = f'POA{i+1}' # POA for DX_i is POA_(i+1)
            poa_status_raw = row.get(poa_status_col_name) if poa_status_col_name in row else None

            if pd.notna(dx_code):
                diagnoses.append({'code': str(dx_code), 'poa': normalize_poa(poa_status_raw)})

        return diagnoses

    def _get_all_procedures(self, row: pd.Series) -> List[Dict[str, pd.Timestamp]]:
        """Extracts all procedure codes and their dates from a row."""
        procedures: List[Dict[str, pd.Timestamp]] = []
        for i in range(1, 11): # Proc1 to Proc10
            proc_code = row.get(f'Proc{i}')
            proc_date = self._parse_date_string(row.get(f'Proc{i}_Date'), row.get(f'Proc{i}_Time'), row.get('EncounterID'))

            if pd.notna(proc_code):
                procedures.append({'code': str(proc_code), 'date': proc_date})
        return procedures

    def _calculate_days_diff(self, date1: pd.Timestamp, date2: pd.Timestamp) -> Optional[int]:
        """
        Calculates the difference in days between two datetime objects.
        Returns None if either date is NaT.
        """
        if pd.isna(date1) or pd.isna(date2):
            return None
        return (date2 - date1).days

    def _get_first_procedure_date_by_code_set(self, procedures: List[Dict[str, pd.Timestamp]], code_set_name: str) -> pd.Timestamp:
        """
        Finds the earliest date among procedures belonging to a specific code set.
        Returns pd.NaT if no procedures from the set are found or dates are missing.
        """
        min_date = pd.NaT
        target_codes = self.code_sets.get(code_set_name, set())

        if not target_codes:
            return pd.NaT # No codes defined for this set

        for proc_entry in procedures:
            if proc_entry['code'] in target_codes and pd.notna(proc_entry['date']):
                if pd.isna(min_date) or proc_entry['date'] < min_date:
                    min_date = proc_entry['date']
        return min_date

    def _get_latest_procedure_date_by_code_set(self, procedures: List[Dict[str, pd.Timestamp]], code_set_name: str) -> pd.Timestamp:
        """
        Finds the latest date among procedures belonging to a specific code set.
        Returns pd.NaT if no procedures from the set are found or dates are missing.
        """
        max_date = pd.NaT
        target_codes = self.code_sets.get(code_set_name, set())

        if not target_codes:
            return pd.NaT # No codes defined for this set

        for proc_entry in procedures:
            if proc_entry['code'] in target_codes and pd.notna(proc_entry['date']):
                if pd.isna(max_date) or proc_entry['date'] > max_date:
                    max_date = proc_entry['date']
        return max_date

    def _check_procedure_timing(self, procedures: List[Dict[str, pd.Timestamp]], ref_date: pd.Timestamp, target_code_set_name: str, min_days: Optional[int] = None, max_days: Optional[int] = None, inclusive_min: bool = True, inclusive_max: bool = True) -> bool:
        """
        Checks if any procedure from a target_code_set_name falls within a specified
        time window relative to a reference date.

        Args:
            procedures (list): List of procedure dictionaries (from _get_all_procedures).
            ref_date (datetime): The reference date (e.g., admission date, first OR procedure date).
            target_code_set_name (str): The name of the code set for procedures to check.
            min_days (int, optional): Minimum number of days after ref_date. Defaults to None.
            max_days (int, optional): Maximum number of days after ref_date. Defaults to None.
            inclusive_min (bool): If True, min_days is inclusive (>=). If False, exclusive (>).
            inclusive_max (bool): If True, max_days is inclusive (<=). If False, exclusive (<).

        Returns:
            bool: True if a qualifying procedure is found, False otherwise.
        """
        if pd.isna(ref_date):
            return False # Cannot check timing without a reference date

        target_codes = self.code_sets.get(target_code_set_name, set())
        if not target_codes:
            return False # No codes defined for this set

        for proc_entry in procedures:
            proc_code = proc_entry['code']
            proc_date = proc_entry['date']

            if proc_code in target_codes and pd.notna(proc_date):
                days_diff = self._calculate_days_diff(ref_date, proc_date)
                if days_diff is None:
                    continue # Skip if date calculation failed for this procedure

                # Apply timing window logic
                is_within_window = True
                if min_days is not None:
                    if inclusive_min and days_diff < min_days:
                        is_within_window = False
                    elif not inclusive_min and days_diff <= min_days:
                        is_within_window = False
                if max_days is not None:
                    if inclusive_max and days_diff > max_days:
                        is_within_window = False
                    elif not inclusive_max and days_diff >= max_days:
                        is_within_window = False

                if is_within_window:
                    return True # Found a procedure within the window
        return False

    def _get_organ_system_from_code(self, code: str, is_dx: bool = True) -> Optional[str]:
        """
        Determines the organ system associated with a given diagnosis or procedure code.
        """
        for system, codes in self.organ_system_mappings.items():
            code_set_name = codes['dx_codes'] if is_dx else codes['proc_codes']
            if code_set_name in self.code_sets and code in self.code_sets[code_set_name]:
                return system
        return None

    def _assign_psi13_risk_category(self, all_diagnoses: List[Dict[str, Optional[str]]], all_procedures: List[Dict[str, pd.Timestamp]]) -> str:
        """
        Assigns a risk category for PSI_13 (Postoperative Sepsis) based on immune function severity.
        Mutually exclusive assignment: highest priority category wins.

        Categories (Priority 1-4):
        1. severe_immune_compromise (SEVEREIMMUNEDX, SEVEREIMMUNEPROC)
        2. moderate_immune_compromise (MODERATEIMMUNEDX, MODERATEIMMUNEPROC)
        3. malignancy_with_treatment (CANCEID + CHEMORADTXPROC)
        4. baseline_risk (default)
        """
        # Priority 1: Severe Immune Compromise
        for dx_entry in all_diagnoses:
            if dx_entry['code'] in self.code_sets.get('SEVEREIMMUNEDX', set()):
                return "severe_immune_compromise"
        for proc_entry in all_procedures:
            if proc_entry['code'] in self.code_sets.get('SEVEREIMMUNEPROC', set()):
                return "severe_immune_compromise"

        # Priority 2: Moderate Immune Compromise
        for dx_entry in all_diagnoses:
            if dx_entry['code'] in self.code_sets.get('MODERATEIMMUNEDX', set()):
                return "moderate_immune_compromise"
        for proc_entry in all_procedures:
            if proc_entry['code'] in self.code_sets.get('MODERATEIMMUNEPROC', set()):
                return "moderate_immune_compromise"

        # Priority 3: Malignancy with Treatment
        has_cancer_dx = any(dx_entry['code'] in self.code_sets.get('CANCEID', set()) for dx_entry in all_diagnoses)
        has_chemorad_proc = any(proc_entry['code'] in self.code_sets.get('CHEMORADTXPROC', set()) for proc_entry in all_procedures)
        if has_cancer_dx and has_chemorad_proc:
            return "malignancy_with_treatment"

        # Priority 4: Baseline Risk
        return "baseline_risk"

    def _assign_psi15_risk_category(self, all_procedures: List[Dict[str, pd.Timestamp]], index_date: pd.Timestamp) -> str:
        """
        Assigns a risk category for PSI_15 based on procedure complexity on the index date.

        Categories:
        - high_complexity (PCLASSHIGH procedures on index_date)
        - moderate_complexity (PCLASSMODERATE procedures on index_date, if not high)
        - low_complexity (default)
        """
        if pd.isna(index_date):
            return "low_complexity" # Cannot determine complexity without index date

        procedures_on_index_date = [
            proc_entry for proc_entry in all_procedures
            if pd.notna(proc_entry['date']) and proc_entry['date'].date() == index_date.date()
        ]

        # Check for high complexity procedures
        for proc_entry in procedures_on_index_date:
            if proc_entry['code'] in self.code_sets.get('PCLASSHIGH', set()):
                return "high_complexity"

        # Check for moderate complexity procedures
        for proc_entry in procedures_on_index_date:
            if proc_entry['code'] in self.code_sets.get('PCLASSMODERATE', set()):
                return "moderate_complexity"

        return "low_complexity"

    def _has_procedures_with_all_dates_missing(self, procedures: List[Dict[str, pd.Timestamp]], code_set_name: str) -> bool:
        """
        Checks if procedures from a given code set exist but all their dates are missing (NaT).
        Returns True if procedures from the set exist, but all their dates are NaT.
        Returns False if no procedures from the set exist, or if at least one has a valid date.
        """
        target_codes = self.code_sets.get(code_set_name, set())
        if not target_codes:
            return False # No codes defined for this set

        found_any_proc_of_type = False
        found_any_valid_date = False

        for proc_entry in procedures:
            if proc_entry['code'] in target_codes:
                found_any_proc_of_type = True
                if pd.notna(proc_entry['date']):
                    found_any_valid_date = True
                    break # Found a valid date, so not "all dates missing"

        return found_any_proc_of_type and not found_any_valid_date

    def _assign_psi14_stratum(self, all_procedures: List[Dict[str, pd.Timestamp]], all_diagnoses: List[Dict[str, Optional[str]]]) -> str:
        """
        Assigns a stratum for PSI_14 (Postoperative Wound Dehiscence) based on the type of
        abdominopelvic surgery (open vs. non-open) and specific timing/diagnosis conditions.
        Open approach takes priority.
        """
        first_abdomip_open_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ABDOMIPOPEN')
        last_recloip_date = self._get_latest_procedure_date_by_code_set(all_procedures, 'RECLOIP')

        # Check for ABWALLCD in any diagnosis position (principal or secondary, regardless of POA)
        has_abwallcd_in_any_position = any(
            dx_entry['code'] in self.code_sets.get('ABWALLCD', set())
            for dx_entry in all_diagnoses
        )

        # Check for presence of procedure types (regardless of date validity)
        has_abdomip_open_proc_present = any(p['code'] in self.code_sets.get('ABDOMIPOPEN', set()) for p in all_procedures)
        has_abdomip_other_proc_present = any(p['code'] in self.code_sets.get('ABDOMIPOTHER', set()) for p in all_procedures)
        has_recloip_proc_present = any(p['code'] in self.code_sets.get('RECLOIP', set()) for p in all_procedures)


        # Conditions for missing dates (procedures exist but all their dates are NaT)
        all_abdomip_open_dates_missing = self._has_procedures_with_all_dates_missing(all_procedures, 'ABDOMIPOPEN')
        all_recloip_dates_missing = self._has_procedures_with_all_dates_missing(all_procedures, 'RECLOIP')


        # --- Evaluate for OPEN APPROACH Stratum ---
        # Must have ABDOMIPOPEN procedure to be in this stratum
        if has_abdomip_open_proc_present:
            # Condition 1: first ABDOMIPOPEN before last RECLOIP
            cond1_open = pd.notna(first_abdomip_open_date) and pd.notna(last_recloip_date) and \
                         first_abdomip_open_date < last_recloip_date
            # Condition 2: All ABDOMIPOPEN dates are missing
            cond2_open = all_abdomip_open_dates_missing
            # Condition 3: All RECLOIP dates are missing
            cond3_open = all_recloip_dates_missing
            # Condition 4: No ABWALLCD diagnosis
            cond4_open = not has_abwallcd_in_any_position

            if cond1_open or cond2_open or cond3_open or cond4_open:
                return "open_approach"

        # --- Evaluate for NON-OPEN APPROACH Stratum ---
        # Condition A: first ABDOMIPOPEN on or after last RECLOIP
        condA_non_open = pd.notna(first_abdomip_open_date) and pd.notna(last_recloip_date) and \
                         first_abdomip_open_date >= last_recloip_date

        # Condition B: Has ABDOMIPOTHER AND (Sub-condition 1 OR Sub-condition 2 OR Sub-condition 3)
        condB_non_open = False
        if has_abdomip_other_proc_present:
            # Sub-condition 1: All ABDOMIPOPEN dates are missing
            sub_cond1_non_open = all_abdomip_open_dates_missing
            # Sub-condition 2: All RECLOIP dates are missing
            sub_cond2_non_open = all_recloip_dates_missing
            # Sub-condition 3: No ABWALLCD diagnosis
            sub_cond3_non_open = not has_abwallcd_in_any_position

            if sub_cond1_non_open or sub_cond2_non_open or sub_cond3_non_open:
                condB_non_open = True

        if condA_non_open or condB_non_open:
            return "non_open_approach"

        # Fallback if no specific stratum is met (should ideally be caught by denominator logic)
        return "unknown_approach"

    def _check_base_exclusions(self, row: pd.Series, psi_code: str) -> Optional[Tuple[str, str]]:
        """
        Applies base exclusion logic common to many PSIs.
        This includes age, MDC, and general data quality checks.

        Args:
            row (pd.Series): A single row of patient encounter data.
            psi_code (str): The code of the PSI being evaluated (e.g., 'PSI_02').

        Returns:
            tuple: (status, reason) if excluded, None otherwise.
        """
        # Retrieve PSI-specific definitions for exclusions
        psi_def = self.psi_definitions.get(psi_code, {})
        # Note: 'indicator' is nested inside the PSI definition in the JSON, get() is safer
        population_type = psi_def.get('indicator', {}).get('population_type')
        age = row.get("AGE")
        if pd.isna(age):
            return "Exclusion", "Data Exclusion: Missing 'AGE' field"
        try:
            if isinstance(age, str):
                age_int = int(float(age))
            else:
                age_int = int(age)
        except (ValueError, TypeError):
            return "Exclusion", f"Data Exclusion: Invalid 'AGE' value: {age}"
        if population_type == "adult" and age_int < 18:
            return "Exclusion", f"Population Exclusion: Age {age_int} < 18 (adult population)"

        # Dynamically determine required fields based on PSI definition's data_quality rules
        # FIX: Added 'Discharge_Disposition' as a universally required field for robust data quality.
        required_fields: List[str] = ['EncounterID', 'AGE', 'SEX', 'MS-DRG', 'MDC', 'Pdx', 'POA1', 'Discharge_Disposition']

        # Map old/incorrect field names from PSI definitions JSON to new/correct ones in DataFrame
        field_name_map = {
            'DISP': 'Discharge_Disposition', # Map 'DISP' from JSON to 'Discharge_Disposition' in DataFrame
            # Add other mappings here if inconsistencies are found for other fields
        }

        # Add fields explicitly marked as required in data_quality section of PSI definition
        for excl_group in psi_def.get('exclusion_criteria', []):
            if excl_group.get('category') == 'data_quality':
                for rule in excl_group.get('rules', []):
                    if rule.get('description') == 'Missing required fields' and 'fields' in rule:
                        for field_def in rule['fields']:
                            original_field_name = field_def['name']
                            # Use the mapped name, or the original if no mapping exists
                            mapped_field_name = field_name_map.get(original_field_name, original_field_name)

                            if mapped_field_name not in required_fields: # Avoid duplicates
                                required_fields.append(mapped_field_name)

        for field in required_fields:
            if field not in row or pd.isna(row.get(field)):
                # Return the mapped field name in the rationale for clarity
                return "Exclusion", f"Data Exclusion: Missing required field '{field}'"

        # Age exclusion logic based on population type
        age = row.get('AGE')
        if pd.isna(age) or not isinstance(age, (int, float)):
             return "Exclusion", "Data Exclusion: Invalid or missing 'AGE'"
        age = int(age) # Convert to int after checking for NaN/type

        if population_type == 'adult':
            if age < 18:
                return "Exclusion", "Population Exclusion: Age < 18"
        elif population_type == 'newborn_only':
            # For newborn PSIs, age < 18 is expected, so no exclusion here.
            pass
        elif population_type in ['maternal_obstetric', 'elective_surgical_only', 'surgical_only', 'abdominopelvic_surgical', 'medical_and_surgical']:
            # For these, age >= 18 is generally expected, but obstetric patients can be any age.
            # Check for specific age criteria in PSI definition, otherwise apply general age < 18.

            # For PSI_05 and PSI_07, obstetric hospitalizations for patients of any age are allowed
            is_obstetric_any_age_allowed = False
            if psi_code in ['PSI_05', 'PSI_07']:
                # Obstetric: MDC == 14 and principal dx in MDC14PRINDX
                mdc = row.get('MDC')
                pdx = row.get('Pdx')
                if pd.notna(mdc) and int(mdc) == 14 and pd.notna(pdx) and str(pdx).strip().upper() in set(code.strip().upper() for code in self.code_sets.get('MDC14PRINDX', set())):
                    is_obstetric_any_age_allowed = True

            if not is_obstetric_any_age_allowed and age < 18:
                         return "Exclusion", "Population Exclusion: Age < 18 and not an obstetric patient"


        # MDC 15 (Newborn) exclusion logic based on population type
        mdc = row.get('MDC')
        if pd.notna(mdc):
            try:
                mdc_int = int(mdc)
                if mdc_int == 15:
                    # Check if Pdx is in MDC15PRINDX (specific to principal DX rule)
                    pdx = row.get('Pdx')
                    if pd.notna(pdx) and str(pdx) in self.code_sets.get('MDC15PRINDX', set()):
                        if population_type != 'newborn_only': # Only exclude if not a newborn-specific PSI
                            return "Exclusion", "Population Exclusion: MDC 15 - Newborn (principal dx in MDC15PRINDX)"
            except ValueError:
                return "Exclusion", "Data Exclusion: Invalid MDC value"

        # MDC 14 (Obstetric) exclusion logic based on population type
        if pd.notna(mdc):
            try:
                mdc_int = int(mdc)
                if mdc_int == 14:
                    # Check if Pdx is in MDC14PRINDX (specific to principal DX rule)
                    pdx = row.get('Pdx')
                    if pd.notna(pdx) and str(pdx) in self.code_sets.get('MDC14PRINDX', set()):
                        if population_type != 'maternal_obstetric' and psi_code not in ['PSI_05', 'PSI_07']: # Only exclude if not an obstetric-specific PSI or PSI_05
                            return "Exclusion", "Population Exclusion: MDC 14 - Obstetric (principal dx in MDC14PRINDX)"
            except ValueError:
                return "Exclusion", "Data Exclusion: Invalid MDC value"

        # Ungroupable DRG exclusion (common)
        drg = str(row.get('MS-DRG')).zfill(3)
        if pd.notna(drg) and drg == '999':
            return "Exclusion", "Data Exclusion: DRG is ungroupable (999)"

        return None # No base exclusion met

    def evaluate_psi(self, row: pd.Series, psi_code: str) -> Tuple[str, str, str, Dict[str, Any]]:
        """
        Evaluates a single patient encounter against a specific PSI.
        This method will call the PSI-specific evaluation function.

        Args:
            row (pd.Series): A single row of patient encounter data.
            psi_code (str): The code of the PSI to evaluate (e.g., 'PSI_02').

        Returns:
            tuple: (status, reason, psi_category, details)
        """
        # Apply base exclusions first
        base_exclusion_result = self._check_base_exclusions(row, psi_code)
        if base_exclusion_result:
            status, reason = base_exclusion_result
            return status, reason, psi_code, {}

        # Call the specific PSI evaluation function
        eval_func_name = "evaluate_" + psi_code.lower().replace("psi_", "psi")
        if hasattr(self, eval_func_name):
            eval_func = getattr(self, eval_func_name)
            try:
                status, reason = eval_func(row, self.code_sets)
                return status, reason, psi_code, {} # Details can be expanded by specific PSI functions
            except Exception as e:
                import traceback
                traceback.print_exc() # Print full traceback for debugging
                return "Error", f"An error occurred during PSI evaluation: {e}", psi_code, {}
        else:
            return "Not Implemented", f"Evaluation logic for {psi_code} not found.", psi_code, {}

    def evaluate_psi02(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_02: Death Rate in Low-Mortality DRGs.

        Denominator: Age >=18, low-mortality DRG (LOWMODR codes), exclude trauma/cancer/immunocompromised,
                     exclude transfers to acute care, exclude hospice admissions, exclude MDC 15.
        Numerator: Death disposition (DISP=20) among eligible cases.
        POA Logic: POA status does not affect exclusions for TRAUMID, CANCEID, IMMUNID.
                   Numerator is based on Discharge_Disposition, so POA is not applicable.
        """
        # Denominator Inclusion: Low-mortality DRG
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('LOWMODR', set())):
            return "Exclusion", "Denominator Exclusion: Not a low-mortality DRG"

        # Denominator Exclusions (Clinical - POA does not matter for these exclusions as per JSON description)
        all_diagnoses = self._get_all_diagnoses(row)
        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('TRAUMID', set())):
                return "Exclusion", f"Denominator Exclusion: Trauma diagnosis present ({dx_code})"
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('CANCEID', set())):
                return "Exclusion", f"Denominator Exclusion: Cancer diagnosis present ({dx_code})"
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('IMMUNID', set())):
                return "Exclusion", f"Denominator Exclusion: Immunocompromised diagnosis present ({dx_code})"

        all_procedures = self._get_all_procedures(row)
        for proc_entry in all_procedures:
            proc_code = proc_entry['code']
            if proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('IMMUNIP', set())):
                return "Exclusion", f"Denominator Exclusion: Immunocompromising procedure present ({proc_code})"

        # Denominator Exclusions (Admission/Transfer)
        point_of_origin = row.get('POINTOFORIGINUB04')
        if pd.notna(point_of_origin) and str(point_of_origin) == 'F':
            return "Exclusion", "Denominator Exclusion: Admission from hospice facility"

        # PSI_02 specific transfer exclusion (Discharge_Disposition = 2)
        discharge_disposition = row.get('Discharge_Disposition')
        if pd.notna(discharge_disposition) and int(discharge_disposition) == 2:
            return "Exclusion", "Population Exclusion: Transfer to acute care facility (Discharge_Disposition=2)"

        # Numerator Check
        if pd.notna(discharge_disposition) and int(discharge_disposition) == 20:
            return "Inclusion", "Inclusion: Death disposition (DISP=20)"
        else:
            # Case is in denominator but not numerator
            return "Exclusion", "Exclusion: Not a death disposition (DISP!=20) but in denominator"

    def _is_psi03_denominator_eligible(self, row: pd.Series, all_diagnoses: List[Dict[str, Optional[str]]], appendix: Dict[str, Set[str]]) -> Tuple[bool, str]:
        """
        Determines if a patient encounter is eligible for the PSI_03 denominator.
        Returns (True, "Reason") if eligible, (False, "Exclusion Reason") otherwise.
        """
        # Denominator Inclusion: Surgical or Medical DRG
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())) and \
           drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('MEDIC2R', set())):
            return False, "Denominator Exclusion: Not a surgical or medical MS-DRG"

        # Denominator Inclusion: Length of Stay >= 3 days
        los = row.get('Length_of_stay')
        if pd.isna(los) or int(los) < 3:
            return False, "Denominator Exclusion: Length of stay less than 3 days or missing"

        if not all_diagnoses:
            return False, "Data Exclusion: No diagnoses found"

        # Denominator Exclusions (Clinical)
        principal_dx_code = all_diagnoses[0]['code']

        # Exclusion: Principal diagnosis of pressure ulcer stage 3/4/unstageable or deep tissue injury (PI~EXD*)
        # This uses the pre-populated self.pi_exd_codes_for_principal_exclusion set.
        if principal_dx_code in self.pi_exd_codes_for_principal_exclusion:
            return False, f"Denominator Exclusion: Principal diagnosis is pressure ulcer/DTI ({principal_dx_code})"

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            # Exclusion: Severe burns or exfoliative skin disorders
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('BURNDX', set())):
                return False, f"Denominator Exclusion: Severe burn diagnosis present ({dx_code})"
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('EXFOLIATXD', set())):
                return False, f"Denominator Exclusion: Exfoliative skin disorder diagnosis present ({dx_code})"

        # Obstetric and Newborn exclusions are handled by _check_base_exclusions
        # Ungroupable DRG and missing data are also handled by _check_base_exclusions

        return True, "Meets Denominator Criteria"

    def _is_psi03_numerator_event(self, all_diagnoses: List[Dict[str, Optional[str]]], appendix: Dict[str, Set[str]]) -> Tuple[bool, str]:
        """
        Determines if a patient encounter qualifies as a PSI_03 numerator event.
        Assumes the encounter has already passed denominator checks.
        """
        for pu_dx_entry in all_diagnoses[1:]: # Iterate through secondary diagnoses (DX1 onwards)
            pu_dx_code = pu_dx_entry['code']
            pu_poa_status = pu_dx_entry['poa']

            # Numerator condition: Stage 3/4 or unstageable pressure ulcer AND not POA (N/U/W/null)
            if pu_poa_status in ['N', 'U', 'W', None] or pd.isna(pu_poa_status):
                # Rule 1: Unspecified anatomic site pressure ulcers automatically qualify
                # Check if the actual ICD-10 code is in any of the unspecified PU code sets
                is_unspecified_pu = False
                for unspecified_set_name in self.unspecified_pu_code_set_names: # Iterate through the names like 'PIUNSPECD'
                    if pu_dx_code in appendix.get(unspecified_set_name, set()):
                        is_unspecified_pu = True
                        break
                if is_unspecified_pu:
                    return True, "Inclusion: Hospital-acquired pressure ulcer (Unspecified site, Stage 3/4 or Unstageable)"

                # Rule 2: Specific anatomic site pressure ulcers
                # Check if the actual ICD-10 code is in any of the specific PU code sets
                matched_pu_code_set_name = None
                for pu_code_set_key in self.all_specific_pu_codes_keys_from_map: # Iterate through keys like 'PISACRALD'
                    if pu_dx_code in appendix.get(pu_code_set_key, set()): # Check if the ICD code is in the set
                        matched_pu_code_set_name = pu_code_set_key
                        break

                if matched_pu_code_set_name:
                    # Get the corresponding DTI exclusion code set name for this anatomic site
                    dti_ex_code_set_name = self.anatomic_site_map.get(matched_pu_code_set_name)
                    if dti_ex_code_set_name:
                        # Check if a DTI for the SAME anatomic site is POA='Y'
                        is_dti_poa_same_site = False
                        dti_exclusion_codes = appendix.get(dti_ex_code_set_name, set())
                        if dti_exclusion_codes:
                            for dti_dx_entry in all_diagnoses: # Check ALL diagnoses (principal or secondary) for POA DTI
                                if dti_dx_entry['code'] in dti_exclusion_codes and dti_dx_entry['poa'] == 'Y':
                                    is_dti_poa_same_site = True
                                    break
                        # If NO DTI for the same anatomic site is POA='Y', then it's a numerator event
                        if not is_dti_poa_same_site:
                            return True, "Inclusion: Hospital-acquired pressure ulcer (Specific site, Stage 3/4 or Unstageable, not excluded by POA DTI)"

        return False, "Exclusion: No qualifying hospital-acquired pressure ulcer identified"

    def evaluate_psi03(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_03: Pressure Ulcer Rate.

        Denominator: Surgical/medical DRG (SURGI2R/MEDIC2R), Age >=18, LOS >= 3 days.
        Numerator: Stage 3/4 pressure ulcer NOT POA, AND NOT excluded by DTI POA at same anatomic site,
                   OR unspecified site pressure ulcer NOT POA.
        Exclusions: Principal DX of pressure ulcer/DTI, severe burns, exfoliative skin disorders,
                    obstetric/newborn discharges, ungroupable DRG, missing data.
        """
        all_diagnoses = self._get_all_diagnoses(row)

        # 1. Denominator Check
        is_denominator_eligible, denom_reason = self._is_psi03_denominator_eligible(row, all_diagnoses, appendix)
        if not is_denominator_eligible:
            return "Exclusion", denom_reason

        # 2. Numerator Check (only if in denominator)
        is_numerator, numerator_reason = self._is_psi03_numerator_event(all_diagnoses, appendix)
        if is_numerator:
            return "Inclusion", numerator_reason
        else:
            # Case is in denominator but not numerator
            return "Exclusion", numerator_reason

    def _check_psi04_stratum_criteria(self, stratum_name: str, row: pd.Series, appendix: Dict[str, Set[str]],
                                      all_diagnoses: List[Dict[str, Optional[str]]],
                                      all_procedures: List[Dict[str, pd.Timestamp]],
                                      first_or_proc_date: pd.Timestamp) -> bool:
        """
        Helper function to check if a patient qualifies for a specific PSI_04 stratum
        based on structured rules.
        Assumes general denominator criteria (DRG, ORPROC presence) are already met.
        """
        stratum_rules = self.psi04_rules.get(stratum_name)
        if not stratum_rules:
            print(f"Warning: No structured rules found for stratum: {stratum_name}")
            return False

        principal_dx_code = all_diagnoses[0]['code'] if all_diagnoses else None
        mdc = row.get('MDC')
        mdc_int = int(mdc) if pd.notna(mdc) else None

        # --- Check Stratum Inclusion Criteria ---
        meets_inclusion = False
        inclusion_rules = stratum_rules.get('inclusion', {})

        # Secondary DX (not POA)
        for code_set_name in inclusion_rules.get('secondary_dx_not_poa', []):
            if any(dx_entry['code'] in appendix.get(code_set_name, set()) and
                   (dx_entry['poa'] in ['N', 'U', 'W', None] or pd.isna(dx_entry['poa']))
                   for dx_entry in all_diagnoses[1:]): # Secondary diagnoses only
                meets_inclusion = True
                break

        # Procedure after OR (if not already included)
        if not meets_inclusion and 'procedure_after_or' in inclusion_rules:
            proc_rule = inclusion_rules['procedure_after_or']
            if self._check_procedure_timing(all_procedures, first_or_proc_date,
                                            proc_rule['code_set'],
                                            min_days=proc_rule['min_days_after_or'],
                                            inclusive_min=proc_rule['inclusive_min']):
                meets_inclusion = True

        if not meets_inclusion:
            return False # Must meet at least one inclusion criterion

        # --- Check Stratum Exclusion Criteria ---
        exclusion_rules = stratum_rules.get('exclusions', {})

        # Principal DX exclusions
        for code_set_name in exclusion_rules.get('principal_dx', []):
            if principal_dx_code and principal_dx_code in appendix.get(code_set_name, set()):
                return False

        # Secondary DX (combined) exclusions (e.g., FTR6GV + FTR6QD)
        for combined_rule in exclusion_rules.get('secondary_dx_combined', []):
            dx_set1 = appendix.get(combined_rule['dx_code_set_1'], set())
            dx_set2 = appendix.get(combined_rule['principal_dx_code_set_2'], set())
            has_dx1 = any(dx_entry['code'] in dx_set1 for dx_entry in all_diagnoses)
            has_dx2_principal = principal_dx_code and principal_dx_code in dx_set2
            if has_dx1 and has_dx2_principal:
                return False

        # Any DX exclusions (any position, any POA status)
        for code_set_name in exclusion_rules.get('any_dx', []):
            if any(dx_entry['code'] in appendix.get(code_set_name, set()) for dx_entry in all_diagnoses):
                return False

        # Any Procedure exclusions
        for code_set_name in exclusion_rules.get('any_proc', []):
            if any(proc_entry['code'] in appendix.get(code_set_name, set()) for proc_entry in all_procedures):
                return False

        # MDC exclusions
        for excluded_mdc in exclusion_rules.get('mdc', []):
            if mdc_int is not None and mdc_int == excluded_mdc:
                return False

        return True # Meets inclusion and no stratum-specific exclusions

    def evaluate_psi04(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_04: Death Rate among Surgical Inpatients with Serious Treatable Complications.

        Denominator: Surgical discharges (SURGI2R), age 18-89 (or obstetric any age), with OR procedures,
                     and elective admission OR OR procedure within 2 days of admission,
                     AND a serious treatable complication from one of the 5 strata.
        Numerator: Death disposition (DISP=20) among eligible cases.
        """
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        admission_date, _ = self._get_admission_discharge_dates(row)

        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: Surgical DRG
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())):
            return "Exclusion", "Denominator Exclusion: Not a surgical MS-DRG"

        # Denominator Inclusion: Age 18-89 OR obstetric patient of any age
        age = row.get('AGE')
        mdc = row.get('MDC')
        principal_dx_code = all_diagnoses[0]['code'] if all_diagnoses else None
        is_obstetric_mdc14 = (pd.notna(mdc) and int(mdc) == 14) and \
                             (principal_dx_code and str(principal_dx_code).strip().upper() in set(code.strip().upper() for code in self.code_sets.get('MDC14PRINDX', set())))

        if not is_obstetric_mdc14:
            if pd.isna(age) or not (18 <= int(age) <= 89):
                return "Exclusion", "Population Exclusion: Age not 18-89 and not an obstetric patient"
        # If it is an obstetric patient (MDC14PRINDX), any age is allowed, so no age exclusion here.

        # Denominator Inclusion: At least one OR procedure
        first_or_proc_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ORPROC')
        if pd.isna(first_or_proc_date):
            return "Exclusion", "Denominator Exclusion: No qualifying OR procedure found"

        # Denominator Inclusion: Admission Timing (Elective OR OR procedure within 2 days of admission)
        admission_type = row.get('ATYPE')
        is_elective_admission = (pd.notna(admission_type) and int(admission_type) == 3)
        or_proc_within_2_days = False
        if pd.notna(admission_date) and pd.notna(first_or_proc_date):
            days_diff = self._calculate_days_diff(admission_date, first_or_proc_date)
            if days_diff is not None and days_diff <= 2:
                or_proc_within_2_days = True

        if not (is_elective_admission or or_proc_within_2_days):
            return "Exclusion", "Denominator Exclusion: Not elective admission and first OR not within 2 days of admission"

        # Apply overall exclusions (from PSI_04 JSON's 'overall_exclusions' category)
        # Transfer to acute care facility (Discharge_Disposition=2)
        discharge_disposition = row.get('Discharge_Disposition')
        if pd.notna(discharge_disposition) and int(discharge_disposition) == 2:
            return "Exclusion", "Overall Exclusion: Transfer to acute care facility (Discharge_Disposition=2)"

        # Admission from hospice facility (POINTOFORIGINUB04='F')
        point_of_origin = row.get('POINTOFORIGINUB04')
        if pd.notna(point_of_origin) and str(point_of_origin) == 'F':
            return "Exclusion", "Overall Exclusion: Admission from hospice facility"

        # Newborn and neonatal discharges (MDC 15 principal diagnosis)
        if pd.notna(mdc) and int(mdc) == 15 and principal_dx_code and str(principal_dx_code) in self.code_sets.get('MDC15PRINDX', set()):
            return "Exclusion", "Overall Exclusion: MDC 15 - Newborn (principal dx in MDC15PRINDX)"


        # Identify qualifying complication stratum (highest priority wins)
        assigned_stratum: Optional[str] = None
        for stratum_name in self.psi04_strata_priority:
            if self._check_psi04_stratum_criteria(stratum_name, row, appendix, all_diagnoses, all_procedures, first_or_proc_date):
                assigned_stratum = stratum_name
                break # Assign to highest priority stratum found

        if assigned_stratum is None:
            return "Exclusion", "Exclusion: No serious treatable complication identified"

        # Numerator Check: Death disposition (Discharge_Disposition=20)
        if pd.notna(discharge_disposition) and int(discharge_disposition) == 20:
            return "Inclusion", f"Inclusion: Death among surgical inpatients with {assigned_stratum.replace('STRATUM_', '').replace('_', ' ')}"
        else:
            return "Exclusion", f"Exclusion: Not a death disposition (Discharge_Disposition!=20) but in {assigned_stratum.replace('STRATUM_', '').replace('_', ' ')} denominator"


    def evaluate_psi05(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_05: Retained Surgical Item or Unretrieved Device Fragment.

        Denominator: Surgical (SURGI2R) or Medical (MEDIC2R) discharges for patients ages 18+
                     OR obstetric discharges (MDC14PRINDX principal dx) for patients of any age.
        Numerator: FOREIID codes (secondary diagnosis, not POA).
        Exclusions:
            - Principal DX of FOREIID
            - Secondary DX of FOREIID present on admission (POA=Y)
            - Principal DX of MDC15PRINDX (newborn)
            - Missing required fields, ungroupable DRG
        """
        all_diagnoses = self._get_all_diagnoses(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Get core data elements
        age = row.get('AGE')
        mdc = row.get('MDC')
        drg = str(row.get('MS-DRG')).zfill(3)
        principal_dx_entry = all_diagnoses[0]
        principal_dx_code = principal_dx_entry['code']

        # Denominator Inclusion: Surgical or Medical DRG
        is_surgical_medical = drg.strip().upper() in set(code.strip().upper() for code in appendix.get('SURGI2R', set())) or drg.strip().upper() in set(code.strip().upper() for code in appendix.get('MEDIC2R', set()))

        if not is_surgical_medical:
            return "Exclusion", "Denominator Exclusion: Not a surgical or medical MS-DRG"

        # Age and Population Logic
        # Check if this is an obstetric case (MDC 14 with principal dx in MDC14PRINDX)
        is_obstetric_case = False
        if pd.notna(mdc) and int(mdc) == 14 and principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('MDC14PRINDX', set())):
            is_obstetric_case = True

        # Age requirement: 18+ for general cases, any age for obstetric cases
        if not is_obstetric_case:
            if pd.isna(age) or int(age) < 18:
                return "Exclusion", "Population Exclusion: Age < 18 and not an obstetric case"

        # Exclusions: Principal diagnosis of retained surgical item
        if principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('FOREIID', set())):
            return "Exclusion", "Numerator Exclusion: Principal diagnosis is retained surgical item"

        # Exclusions: Principal diagnosis of newborn (MDC15PRINDX)
        if principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('MDC15PRINDX', set())):
            return "Exclusion", "Numerator Exclusion: Principal diagnosis is newborn condition (MDC15PRINDX)"

        # Check secondary diagnoses for retained surgical items
        has_qualifying_retained_item = False

        # Start from the first secondary diagnosis (DX1) onwards
        for dx_entry in all_diagnoses[1:]:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']

            # Check if this is a retained surgical item code
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('FOREIID', set())):
                # Exclude if present on admission
                if poa_status == 'Y':
                    return "Exclusion", f"Numerator Exclusion: Retained surgical item ({dx_code}) present on admission (POA=Y)"

                # Include if NOT present on admission (N, U, W, or missing)
                if poa_status in ['N', 'U', 'W', None] or pd.isna(poa_status):
                    has_qualifying_retained_item = True
                    break

        # Numerator determination
        if has_qualifying_retained_item:
            population_type = "obstetric" if is_obstetric_case else "general surgical/medical"
            return "Inclusion", f"Inclusion: Retained surgical item or unretrieved device fragment (secondary, not POA) - {population_type} population"
        else:
            return "Exclusion", "Exclusion: No qualifying retained surgical item found"

    def evaluate_psi06(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_06: Iatrogenic Pneumothorax.

        Denominator: Surgical or Medical DRG (SURGI2R/MEDIC2R), Age >=18.
        Numerator: IATROID codes (secondary, not POA).
        Exclusions: IATPTXD (principal or secondary POA='Y'), CTRAUMD (any), PLEURAD (any),
                    THORAIP procedures (any), CARDSIP procedures (any),
                    Obstetric MDC 14, Newborn MDC 15 (per official AHRQ spec).
        """
        # Denominator Inclusion: Surgical or Medical DRG (Age >=18 handled by base exclusions)
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())) and drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('MEDIC2R', set())):
            return "Exclusion", "Denominator Exclusion: Not a surgical or medical MS-DRG"

        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Exclusions (Diagnoses)
        principal_dx_entry = all_diagnoses[0]
        principal_dx_code = principal_dx_entry['code']

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']

            # Exclusion: IATPTXD (non-traumatic pneumothorax) if principal or secondary POA='Y'
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('IATPTXD', set())):
                # Check if it's the principal diagnosis OR if it's secondary and POA='Y'
                if (dx_entry == principal_dx_entry) or (poa_status == 'Y'):
                    return "Exclusion", f"Denominator Exclusion: Non-traumatic pneumothorax ({dx_code}) present on admission or as principal diagnosis"

            # Exclusion: CTRAUMD (chest trauma) any position
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('CTRAUMD', set())):
                return "Exclusion", f"Denominator Exclusion: Chest trauma diagnosis present ({dx_code})"

            # Exclusion: PLEURAD (pleural effusion) any position
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PLEURAD', set())):
                return "Exclusion", f"Denominator Exclusion: Pleural effusion diagnosis present ({dx_code})"

        # ---------- MDC 14/15 Exclusion Block Inserted Here ----------
        # Exclusion: MDC 14 obstetric discharges
        mdc = row.get('MDC')
        if pd.notna(mdc):
            try:
                mdc_int = int(mdc)
                if mdc_int == 14:
                    if principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('MDC14PRINDX', set())):
                        return "Exclusion", "Denominator Exclusion: Obstetric discharge (MDC 14 - principal dx in MDC14PRINDX)"
                # Exclusion: MDC 15 newborn discharges
                if mdc_int == 15:
                    if principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('MDC15PRINDX', set())):
                        return "Exclusion", "Denominator Exclusion: Newborn discharge (MDC 15 - principal dx in MDC15PRINDX)"
            except ValueError:
                return "Exclusion", "Data Exclusion: Invalid MDC value"
        # ------------------------------------------------------------

        # Exclusions (Procedures)
        for proc_entry in all_procedures:
            proc_code = proc_entry['code']
            # Exclusion: THORAIP (thoracic surgery) procedures
            if proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('THORAIP', set())):
                return "Exclusion", f"Denominator Exclusion: Thoracic surgery procedure present ({proc_code})"
            # Exclusion: CARDSIP (trans-pleural cardiac) procedures
            if proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('CARDSIP', set())):
                return "Exclusion", f"Denominator Exclusion: Trans-pleural cardiac procedure present ({proc_code})"

        # Numerator Check: IATROID (iatrogenic pneumothorax) secondary and not POA
        has_iatrogenic_pneumothorax = False
        # Start from the first secondary diagnosis (DX1) onwards
        for dx_entry in all_diagnoses[1:]:
            if dx_entry['code'] in appendix.get('IATROID', set()) and (dx_entry['poa'] in ['N', 'U', 'W', None] or pd.isna(dx_entry['poa'])):
                has_iatrogenic_pneumothorax = True
                break

        if has_iatrogenic_pneumothorax:
            return "Inclusion", "Inclusion: Iatrogenic pneumothorax (secondary, not POA)"
        else:
            return "Exclusion", "Exclusion: No qualifying iatrogenic pneumothorax found"



    def evaluate_psi07(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_07: Central Venous Catheter-Related Bloodstream Infection Rate.
        Implements AHRQ logic: Obstetric patients (MDC 14 + Pdx in MDC14PRINDX) are eligible at any age.
        """
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found (Required for PSI 07 evaluation)"

        age = row.get('AGE')
        mdc = row.get('MDC')
        drg = str(row.get('MS-DRG')).zfill(3)
        los = row.get('Length_of_stay')
        principal_dx_code = all_diagnoses[0]['code'] if all_diagnoses else None

        # 1b. Obstetric (MDC14) with principal DX in MDC14PRINDX, any age (PATCH: always include if true)
        is_obstetric_eligible = False
        if pd.notna(mdc) and int(mdc) == 14:
            if principal_dx_code and principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('MDC14PRINDX', set())):
                is_obstetric_eligible = True

        if is_obstetric_eligible:
            # Obstetric patients are always eligible, regardless of age
            pass  # allow downstream exclusion logic to run (LOS, cancer/immuno exclusion, etc.)

        # 1a. Surgical/medical, age >=18
        is_surgical_medical_eligible = False
        if drg.strip().upper() in set(code.strip().upper() for code in appendix.get('SURGI2R', set())) or drg.strip().upper() in set(code.strip().upper() for code in appendix.get('MEDIC2R', set())):
            if pd.notna(age) and int(age) >= 18:
                is_surgical_medical_eligible = True

        # Require at least one denominator inclusion
        if not (is_surgical_medical_eligible or is_obstetric_eligible):
            if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())) and drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('MEDIC2R', set())):
                return "Exclusion", f"Denominator Exclusion: Not a surgical or medical MS-DRG (DRG={drg})"
            elif pd.isna(age) or int(age) < 18:
                return "Exclusion", f"Population Exclusion: Age < 18 and not an obstetric-eligible patient (AGE={age})"
            else:
                return "Exclusion", "Denominator Exclusion: Does not meet surgical/medical or obstetric criteria"

        # --- 2. Denominator Exclusions (as per AHRQ) ---
        # 2a. Length of stay < 2 days
        if pd.isna(los) or int(los) < 2:
            return "Exclusion", f"Denominator Exclusion: Length of stay less than 2 days (LOS={los})"

        # 2b. Principal DX = IDTMC3D (central venous catheter infection)
        if principal_dx_code and principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('IDTMC3D', set())):
            return "Exclusion", f"Denominator Exclusion: Principal diagnosis is central venous catheter-related bloodstream infection (Code={principal_dx_code}, Appendix=IDTMC3D)"

        # 2c. Principal DX = MDC15PRINDX (Newborn/Neonate)
        if principal_dx_code and principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('MDC15PRINDX', set())):
            return "Exclusion", f"Denominator Exclusion: Principal diagnosis assigned to MDC 15 Newborns & Other Neonates (Code={principal_dx_code}, Appendix=MDC15PRINDX)"

        # 2d. Secondary DX = IDTMC3D with POA=Y
        for i, dx_entry in enumerate(all_diagnoses[1:], 1):
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('IDTMC3D', set())) and poa_status == 'Y':
                return "Exclusion", f"Denominator Exclusion: Central venous catheter infection present on admission (Code={dx_code}, DX{i}, POA{i+1}=Y, Appendix=IDTMC3D)"

        # 2e. Any listed DX = CANCEID
        for i, dx_entry in enumerate(all_diagnoses):
            dx_code = dx_entry['code']
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('CANCEID', set())):
                dx_pos = "Principal" if i == 0 else f"DX{i}"
                poa_disp = dx_entry['poa'] if 'poa' in dx_entry else None
                return "Exclusion", f"Denominator Exclusion: Cancer diagnosis present ({dx_code}, {dx_pos}, POA={poa_disp}, Appendix=CANCEID)"

        # 2f. Any listed DX = IMMUNID
        for i, dx_entry in enumerate(all_diagnoses):
            dx_code = dx_entry['code']
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('IMMUNID', set())):
                dx_pos = "Principal" if i == 0 else f"DX{i}"
                poa_disp = dx_entry['poa'] if 'poa' in dx_entry else None
                return "Exclusion", f"Denominator Exclusion: Immunocompromised state diagnosis present ({dx_code}, {dx_pos}, POA={poa_disp}, Appendix=IMMUNID)"

        # 2g. Any listed procedure = IMMUNIP
        for j, proc_entry in enumerate(all_procedures, 1):
            proc_code = proc_entry['code']
            if proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('IMMUNIP', set())):
                return "Exclusion", f"Denominator Exclusion: Immunocompromised state procedure present (Proc{j}={proc_code}, Appendix=IMMUNIP)"

        # 2h, 2i handled in base exclusions

        # 3. Numerator: Secondary DX = IDTMC3D, POA ≠ Y
        has_qualifying_cvc_bsi = False
        qualifying_dx_code = None
        qualifying_dx_position = None
        qualifying_poa = None

        for i, dx_entry in enumerate(all_diagnoses[1:], 1):
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('IDTMC3D', set())):
                if poa_status in ['N', 'U', 'W', None] or pd.isna(poa_status):
                    has_qualifying_cvc_bsi = True
                    qualifying_dx_code = dx_code
                    qualifying_dx_position = f"DX{i}"
                    qualifying_poa = poa_status
                    break

        if has_qualifying_cvc_bsi:
            population_type = "obstetric" if is_obstetric_eligible else "surgical/medical"
            return (
                "Inclusion",
                f"Inclusion: Central venous catheter-related bloodstream infection "
                f"(Secondary diagnosis: {qualifying_dx_code}, {qualifying_dx_position}, POA={qualifying_poa}, "
                f"Appendix=IDTMC3D, Population={population_type})"
            )
        else:
            return "Exclusion", "Exclusion: No qualifying secondary diagnosis for central venous catheter-related bloodstream infection (per PSI 07 spec)"

    def evaluate_psi08(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        # FAILSAFE: Explicit age check for PSI_08 (should be caught by base exclusions)
        age = row.get("AGE")
        if pd.isna(age):
            return "Exclusion", "Data Exclusion: Missing AGE field"
        try:
            age_int = int(float(age)) if isinstance(age, str) else int(age)
        except (ValueError, TypeError):
            return "Exclusion", f"Data Exclusion: Invalid AGE value: {age}"
        if age_int < 18:
            return "Exclusion", f"Population Exclusion: Age {age_int} < 18 (PSI_08 requires adult population)"
        """
        Evaluates a patient encounter for PSI_08: In-Hospital Fall-Associated Fracture Rate.

        Denominator: Surgical or medical discharges for patients ages 18 years and older.
        Numerator: In-hospital fall-associated fractures (secondary diagnosis, not POA),
                   categorized hierarchically as Hip Fracture (priority) or Other Fracture.
        Exclusions: Principal DX of fracture, secondary DX of fracture POA='Y',
                    joint prosthesis-associated fracture, obstetric/neonatal discharges.
        """
        all_diagnoses = self._get_all_diagnoses(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: Surgical or Medical DRG (Age >=18 handled by base exclusions)
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())) and drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('MEDIC2R', set())):
            return "Exclusion", "Denominator Exclusion: Not a surgical or medical MS-DRG"

        # Exclusions (Fracture Diagnoses)
        principal_dx_entry = all_diagnoses[0]
        principal_dx_code = principal_dx_entry['code']

        # Exclusion: Principal diagnosis of fracture (FXID*)
        if principal_dx_code and principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('FXID', set())):
            return "Exclusion", f"Denominator Exclusion: Principal diagnosis is fracture ({principal_dx_code})"

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']

            # Exclusion: Secondary diagnosis of fracture (FXID*) present on admission (POA='Y')
            if dx_entry != principal_dx_entry and dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('FXID', set())) and poa_status == 'Y':
                return "Exclusion", f"Denominator Exclusion: Secondary fracture diagnosis ({dx_code}) present on admission (POA=Y)"

            # Exclusion: Any diagnosis of joint prosthesis-associated fracture (PROSFXID*)
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PROSFXID', set())):
                return "Exclusion", f"Denominator Exclusion: Joint prosthesis-associated fracture present ({dx_code})"

        # Numerator Identification & Hierarchy
        # Collect all non-POA secondary fractures
        non_poa_secondary_fractures: List[str] = []
        for dx_entry in all_diagnoses[1:]: # Iterate through secondary diagnoses
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('FXID', set())) and (poa_status in ['N', 'U', 'W', None] or pd.isna(poa_status)):
                non_poa_secondary_fractures.append(dx_code)

        if not non_poa_secondary_fractures:
            return "Exclusion", "Exclusion: No qualifying in-hospital fall-associated fracture found"

        # Apply hierarchy: Hip Fracture takes priority
        has_hip_fracture = False
        for fx_code in non_poa_secondary_fractures:
            if fx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('HIPFXID', set())):
                has_hip_fracture = True
                break

        if has_hip_fracture:
            return "Inclusion", "Inclusion: In-hospital fall-associated Hip Fracture"
        else:
            # If no hip fracture, but other non-POA secondary fractures exist
            return "Inclusion", "In-hospital fall-associated Other Fracture"

    def evaluate_psi09(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_09: Postoperative Hemorrhage or Hematoma Rate.

        Denominator: Surgical DRG (SURGI2R), Age >=18.
        Numerator: POHMRI2D diagnosis (secondary, not POA) AND HEMOTH2P procedure.
        HEMOTH2P must occur AFTER the first ORPROC.
        Exclusions: COAGDID (any), MEDBLEEDD (principal or secondary POA='Y'),
        THROMBOLYTICP (before or same day as first HEMOTH2P).
        """
        # FAILSAFE: Explicit age check for PSI_09 based on its stated denominator requirement
        age = row.get("AGE")
        if pd.isna(age):
            return "Exclusion", "Data Exclusion: Missing AGE field"
        try:
            age_int = int(float(age)) if isinstance(age, str) else int(age)
        except (ValueError, TypeError):
            return "Exclusion", f"Data Exclusion: Invalid AGE value: {age}"
        if age_int < 18:
            return "Exclusion", f"Population Exclusion: Age {age_int} < 18 (PSI_09 requires adult population)"

        # Base exclusions
        base_exclusion = self._check_base_exclusions(row, 'PSI_09')
        if base_exclusion:
            return base_exclusion

        # Denominator Inclusion: Surgical DRG
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())):
            return "Exclusion", "Denominator Exclusion: Not a surgical MS-DRG"

        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Principal diagnosis exclusion
        principal_dx_code = all_diagnoses[0]['code']
        if principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('POHMRI2D', set())):
            return "Exclusion", f"Denominator Exclusion: Principal diagnosis is postoperative hemorrhage/hematoma ({principal_dx_code})"

        # Explicit OR procedure requirement
        or_procedures = [p for p in all_procedures if p['code'] in appendix.get('ORPROC', set())]
        if not or_procedures:
            return "Exclusion", "Denominator Exclusion: No qualifying OR procedure found"

        # Only OR procedure is HEMOTH2P
        hemoth2p_procedures = [p for p in all_procedures if p['code'] in appendix.get('HEMOTH2P', set())]
        if len(or_procedures) == 1 and len(hemoth2p_procedures) >= 1:
            if or_procedures[0]['code'] in appendix.get('HEMOTH2P', set()):
                return "Exclusion", "Denominator Exclusion: Only OR procedure is for hemorrhage treatment"

        # Get first OR procedure date
        first_or_proc_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ORPROC')
        if pd.isna(first_or_proc_date):
            return "Exclusion", "Denominator Exclusion: No qualifying OR procedure found for timing reference"

        # --- NEW EXCLUSION: HEMOTH2P occurs before first ORPROC ---
        first_hemoth2p_date_for_timing = self._get_first_procedure_date_by_code_set(all_procedures, 'HEMOTH2P')
        if pd.notna(first_hemoth2p_date_for_timing) and pd.notna(first_or_proc_date):
            if first_hemoth2p_date_for_timing < first_or_proc_date:
                return "Exclusion", "Denominator Exclusion: Treatment of hemorrhage/hematoma occurred before first OR procedure"
        # --- END NEW EXCLUSION ---

        # Exclusions (Diagnoses)
        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('COAGDID', set())):
                return "Exclusion", "Denominator Exclusion: Coagulation disorder diagnosis present ({dx_code})"
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('MEDBLEEDD', set())):
                if dx_entry == all_diagnoses[0] or poa_status == 'Y':
                    return "Exclusion", f"Denominator Exclusion: Medication-related coagulopathy ({dx_code}) present on admission or as principal diagnosis"

        # Exclusions (Procedures) - Thrombolytic timing
        first_hemoth2p_date = self._get_first_procedure_date_by_code_set(all_procedures, 'HEMOTH2P')
        if pd.notna(first_hemoth2p_date):
            if self._check_procedure_timing(all_procedures, first_hemoth2p_date, 'THROMBOLYTICP', max_days=0, inclusive_max=True):
                return "Exclusion", "Denominator Exclusion: Thrombolytic medication before or same day as hemorrhage treatment"

        # Numerator Check
        has_postop_hemorrhage_dx = any(
            dx['code'] in appendix.get('POHMRI2D', set()) and (dx['poa'] in ['N', 'U', 'W', None] or pd.isna(dx['poa']))
            for dx in all_diagnoses[1:]
        )
        if has_postop_hemorrhage_dx:
            # The numerator condition requires HEMOTH2P to occur *after* the first ORPROC.
            # The _check_procedure_timing function with min_days=0, inclusive_min=False
            # correctly implements "strictly after".
            if self._check_procedure_timing(all_procedures, first_or_proc_date, 'HEMOTH2P', min_days=0, inclusive_min=False):
                return "Inclusion", "Inclusion: Postoperative hemorrhage/hematoma with treatment (secondary, not POA)"

        return "Exclusion", "Exclusion: No qualifying postoperative hemorrhage/hematoma found with required treatment and timing"


    def evaluate_psi10(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_10: Postoperative Acute Kidney Injury Requiring Dialysis Rate.

        Denominator: Elective surgical discharges (ATYPE=3) for patients ages 18 years and older with OR procedures.
        Numerator: Postoperative acute kidney failure (PHYSIDB secondary, not POA) AND dialysis procedure (DIALYIP)
                   after the primary OR procedure.
        Exclusions:
            - Principal DX of PHYSIDB or Secondary DX of PHYSIDB POA='Y'.
            - DIALYIP or DIALY2P procedures before or same day as first ORPROC.
            - Principal DX of CARDIID, CARDRID, SHOCKID or Secondary DX of these POA='Y'.
            - Principal DX of CRENLFD or Secondary DX of CRENLFD POA='Y'.
            - Principal DX of URINARYOBSID.
            - SOLKIDD POA='Y' AND PNEPHREP procedure.
            - Obstetric/neonatal discharges.
        """
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: Elective Surgical Population (Age >=18 handled by base exclusions)
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())):
            return "Exclusion", "Denominator Exclusion: Not a surgical MS-DRG"

        admission_type = row.get('ATYPE')
        if pd.isna(admission_type) or int(admission_type) != 3:
            return "Exclusion", "Denominator Exclusion: Admission not elective (ATYPE != 3)"

        # Denominator Inclusion: At least one OR procedure
        first_or_proc_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ORPROC')
        if pd.isna(first_or_proc_date):
            return "Exclusion", "Denominator Exclusion: No qualifying OR procedure found"

        # Exclusions (Diagnoses)
        principal_dx_entry = all_diagnoses[0]
        principal_dx_code = principal_dx_entry['code']

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']

            # Exclusion: Principal DX of PHYSIDB or Secondary DX of PHYSIDB POA='Y'
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PHYSIDB', set())):
                if (dx_entry == principal_dx_entry) or (poa_status == 'Y'):
                    return "Exclusion", f"Denominator Exclusion: Acute kidney failure ({dx_code}) present on admission or as principal diagnosis"

            # Exclusion: Cardiac Conditions (CARDIID, CARDRID)
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('CARDIID', set())) or dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('CARDRID', set())):
                if (dx_entry == principal_dx_entry) or (poa_status == 'Y'):
                    return "Exclusion", f"Denominator Exclusion: Cardiac condition ({dx_code}) present on admission or as principal diagnosis"

            # Exclusion: Shock Conditions (SHOCKID)
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('SHOCKID', set())):
                if (dx_entry == principal_dx_entry) or (poa_status == 'Y'):
                    return "Exclusion", f"Denominator Exclusion: Shock condition ({dx_code}) present on admission or as principal diagnosis"

            # Exclusion: Chronic Kidney Disease (CRENLFD)
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('CRENLFD', set())):
                if (dx_entry == principal_dx_entry) or (poa_status == 'Y'):
                    return "Exclusion", f"Denominator Exclusion: Chronic kidney disease ({dx_code}) present on admission or as principal diagnosis"

            # Exclusion: Urinary Obstruction (URINARYOBSID) - only principal
            if dx_entry == principal_dx_entry and dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('URINARYOBSID', set())):
                return "Exclusion", f"Denominator Exclusion: Principal diagnosis is urinary tract obstruction ({dx_code})"

        # Exclusions (Procedures)
        # Dialysis Timing Exclusions (DIALYIP, DIALY2P before/same day as first ORPROC)
        if self._check_procedure_timing(all_procedures, first_or_proc_date, 'DIALYIP', max_days=0, inclusive_max=True):
            return "Exclusion", "Denominator Exclusion: Dialysis procedure before or same day as first OR procedure"
        if self._check_procedure_timing(all_procedures, first_or_proc_date, 'DIALY2P', max_days=0, inclusive_max=True):
            return "Exclusion", "Denominator Exclusion: Dialysis access procedure before or same day as first OR procedure"

        # Solitary Kidney Nephrectomy (SOLKIDD POA='Y' AND PNEPHREP procedure)
        has_solkid_poa = any(dx_entry['code'] in appendix.get('SOLKIDD', set()) and dx_entry['poa'] == 'Y' for dx_entry in all_diagnoses)
        has_pnephrep_proc = any(proc_entry['code'] in appendix.get('PNEPHREP', set()) for proc_entry in all_procedures)
        if has_solkid_poa and has_pnephrep_proc:
            return "Exclusion", "Denominator Exclusion: Solitary kidney present on admission with nephrectomy procedure"

        # Numerator Check: PHYSIDB (secondary, not POA) AND DIALYIP (after first ORPROC)
        has_aki_dx = False
        for dx_entry in all_diagnoses[1:]: # Iterate through secondary diagnoses
            if dx_entry['code'] in appendix.get('PHYSIDB', set()) and (dx_entry['poa'] in ['N', 'U', 'W', None] or pd.isna(dx_entry['poa'])):
                has_aki_dx = True
                break

        if has_aki_dx:
            # Check for DIALYIP procedure strictly after first ORPROC
            if self._check_procedure_timing(all_procedures, first_or_proc_date, 'DIALYIP', min_days=0, inclusive_min=False):
                return "Inclusion", "Inclusion: Postoperative acute kidney injury requiring dialysis"

        return "Exclusion", "Exclusion: No qualifying postoperative acute kidney injury requiring dialysis found"

    def evaluate_psi11(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_11: Postoperative Respiratory Failure Rate.

        Denominator: Elective surgical discharges (ATYPE=3) for patients ages 18 years and older with OR procedures.
        Numerator: Discharges with ANY of the following postoperative respiratory complications:
            - Acute postprocedural respiratory failure (ACURF2D secondary, not POA).
            - Mechanical ventilation > 96 consecutive hours (PR9672P) >= 0 days after first major OR procedure.
            - Mechanical ventilation 24-96 consecutive hours (PR9671P) >= 2 days after first major OR procedure.
            - Intubation procedure (PR9604P) >= 1 day after after first major OR procedure.
        Exclusions:
            - Principal DX of ACURF3D or Secondary DX of ACURF3D POA='Y'.
            - Any DX of TRACHID POA='Y'.
            - Only OR procedure is TRACHIP or TRACHIP before first ORPROC.
            - Any DX of MALHYPD.
            - Any DX of NEUROMD POA='Y'.
            - Any DX of DGNEUID POA='Y'.
            - High-risk surgeries: NUCRANP, PRESOPP, LUNGCIP, LUNGTRANSP.
            - MDC 4 (Respiratory System).
            - Obstetric/neonatal discharges.
        """
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: Elective Surgical Population (Age >=18 handled by base exclusions)
        drg = str(row.get('MS-DRG')).zfill(3)
        # Modified to check for both surgical and medical DRGs, similar to PSI_05 and PSI_06
        is_surgical_medical = drg.strip().upper() in set(code.strip().upper() for code in appendix.get('SURGI2R', set())) or \
                              drg.strip().upper() in set(code.strip().upper() for code in appendix.get('MEDIC2R', set()))
        if not is_surgical_medical:
            return "Exclusion", "Denominator Exclusion: Not a surgical or medical MS-DRG"

        admission_type = row.get('ATYPE')
        if pd.isna(admission_type) or int(admission_type) != 3:
            return "Exclusion", "Denominator Exclusion: Admission not elective (ATYPE != 3)"

        # Denominator Inclusion: At least one OR procedure
        first_or_proc_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ORPROC')
        if pd.isna(first_or_proc_date):
            return "Exclusion", "Denominator Exclusion: No qualifying OR procedure found"

        # Exclusions (Diagnoses)
        principal_dx_entry = all_diagnoses[0]
        principal_dx_code = principal_dx_entry['code']

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']

            # Exclusion: Principal DX of ACURF3D or Secondary DX of ACURF3D POA='Y'
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('ACURF3D', set())):
                if (dx_entry == principal_dx_entry) or (poa_status == 'Y'):
                    return "Exclusion", f"Denominator Exclusion: Acute respiratory failure ({dx_code}) present on admission or as principal diagnosis"

            # Exclusion: Any DX of TRACHID POA='Y'
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('TRACHID', set())) and poa_status == 'Y':
                return "Exclusion", f"Denominator Exclusion: Tracheostomy diagnosis ({dx_code}) present on admission"

            # Exclusion: Any DX of MALHYPD
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('MALHYPD', set())):
                return "Exclusion", "Denominator Exclusion: Malignant hyperthermia diagnosis present ({dx_code})"

            # Exclusion: Any DX of NEUROMD POA='Y'
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('NEUROMD', set())) and poa_status == 'Y':
                return "Exclusion", f"Denominator Exclusion: Neuromuscular disorder ({dx_code}) present on admission"

            # Exclusion: Any DX of DGNEUID POA='Y'
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('DGNEUID', set())) and poa_status == 'Y':
                return "Exclusion", f"Denominator Exclusion: Degenerative neurological disorder ({dx_code}) present on admission"

        # Exclusions (Procedures)
        # Only OR procedure is tracheostomy (TRACHIP)
        or_procedures_in_orproc_set = [p for p in all_procedures if p['code'] in appendix.get('ORPROC', set())]
        if len(or_procedures_in_orproc_set) == 1 and or_procedures_in_orproc_set[0]['code'] in appendix.get('TRACHIP', set()):
            return "Exclusion", "Denominator Exclusion: Only OR procedure is tracheostomy"

        # Tracheostomy (TRACHIP) occurs before first OR procedure
        first_trach_proc_date = self._get_first_procedure_date_by_code_set(all_procedures, 'TRACHIP')
        if pd.notna(first_trach_proc_date) and pd.notna(first_or_proc_date) and first_trach_proc_date < first_or_proc_date:
            return "Exclusion", "Denominator Exclusion: Tracheostomy procedure before first OR procedure"

        # High-risk surgeries
        for proc_entry in all_procedures:
            proc_code = proc_entry['code']
            if proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('NUCRANP', set())) or \
               proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PRESOPP', set())) or \
               proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('LUNGCIP', set())) or \
               proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('LUNGTRANSP', set())):
                return "Exclusion", f"Denominator Exclusion: High-risk surgery procedure present ({proc_code})"

        # MDC 4 (Respiratory System) Exclusion
        mdc = row.get('MDC')
        if pd.notna(mdc) and int(mdc) == 4:
            return "Exclusion", "Denominator Exclusion: MDC 4 (Diseases & Disorders of the Respiratory System)"

        # Numerator Logic (ANY of the following criteria):
        has_postop_respiratory_complication = False

        # 1. Acute postprocedural respiratory failure (ACURF2D secondary, not POA)
        for dx_entry in all_diagnoses[1:]: # Secondary diagnoses
            if dx_entry['code'] in appendix.get('ACURF2D', set()) and (dx_entry['poa'] in ['N', 'U', 'W', None] or pd.isna(dx_entry['poa'])):
                has_postop_respiratory_complication = True
                break

        if not has_postop_respiratory_complication:
            # 2. Mechanical ventilation > 96 consecutive hours (PR9672P) >= 0 days after first major OR procedure
            if self._check_procedure_timing(all_procedures, first_or_proc_date, 'PR9672P', min_days=0, inclusive_min=True):
                has_postop_respiratory_complication = True

        if not has_postop_respiratory_complication:
            # 3. Mechanical ventilation 24-96 consecutive hours (PR9671P) >= 2 days after first major OR procedure
            if self._check_procedure_timing(all_procedures, first_or_proc_date, 'PR9671P', min_days=2, inclusive_min=True):
                has_postop_respiratory_complication = True

        if not has_postop_respiratory_complication:
            # 4. Intubation procedure (PR9604P) >= 1 day after first major OR procedure
            if self._check_procedure_timing(all_procedures, first_or_proc_date, 'PR9604P', min_days=1, inclusive_min=True):
                has_postop_respiratory_complication = True

        if has_postop_respiratory_complication:
            return "Inclusion", "Inclusion: Postoperative respiratory failure"
        else:
            return "Exclusion", "Exclusion: No qualifying postoperative respiratory complication found"

    def evaluate_psi12(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_12: Perioperative Pulmonary Embolism or Deep Vein Thrombosis Rate.

        Denominator: Surgical DRG (SURGI2R), Age >=18, at least one OR procedure.
        Numerator: DEEPVIB or PULMOID (secondary, not POA).
        Exclusions:
            - Principal DX of DEEPVIB or PULMOID.
            - Secondary DX of DEEPVIB or PULMOID POA='Y'.
            - VENACIP or THROMP procedures before or same day as first ORPROC.
            - First OR procedure >=10 days after admission.
            - HITD (secondary, any), NEURTRAD (any POA), ECMOP procedure (any).
            - NEW EXCLUSION: Only OR procedure is VENACIP and/or THROMP.
        """
        # Denominator Inclusion: Surgical DRG (Age >=18 handled by base exclusions)
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())):
            return "Exclusion", "Denominator Exclusion: Not a surgical MS-DRG"
        # The following lines were incorrectly indented and redundant.
        # drg = str(row.get('MS-DRG')).zfill(3)
        # if drg not in appendix.get('SURGI2R', set()) and drg not in appendix.get('MEDIC2R', set()):
        #     return "Exclusion", f"Denominator Exclusion: Not a surgical or medical MS-DRG (DRG={drg})"
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        admission_date, _ = self._get_admission_discharge_dates(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: At least one OR procedure
        first_or_proc_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ORPROC')
        if pd.isna(first_or_proc_date):
            return "Exclusion", "Denominator Exclusion: No qualifying OR procedure found"

        # Get all OR procedures specifically for the "only OR procedure" check
        all_or_procedures = [p for p in all_procedures if p['code'] in appendix.get('ORPROC', set())]

        # NEW EXCLUSION: Only OR procedure(s) is/are for interruption of vena cava (VENACIP) and/or pulmonary arterial or dialysis access thrombectomy (THROMP)
        venacip_codes = appendix.get('VENACIP', set())
        thromp_codes = appendix.get('THROMP', set())
        
        # Check if there are OR procedures and all of them are either VENACIP or THROMP
        if all_or_procedures: # Ensure there's at least one OR procedure
            is_only_venacip_or_thromp = True
            for proc_entry in all_or_procedures:
                if proc_entry['code'] not in venacip_codes and proc_entry['code'] not in thromp_codes:
                    is_only_venacip_or_thromp = False
                    break
            
            if is_only_venacip_or_thromp:
                return "Exclusion", "Denominator Exclusion: Only OR procedure(s) are for vena cava interruption or thrombectomy"


        # Exclusions (Timing-based)
        # VENACIP or THROMP procedures before/same day as first OR
        if self._check_procedure_timing(all_procedures, first_or_proc_date, 'VENACIP', max_days=0, inclusive_max=True):
            return "Exclusion", "Denominator Exclusion: Vena cava interruption before or same day as first OR procedure"
        if self._check_procedure_timing(all_procedures, first_or_proc_date, 'THROMP', max_days=0, inclusive_max=True):
            return "Exclusion", "Denominator Exclusion: Thrombectomy before or same day as first OR procedure"

        # Late surgery exclusion: first OR >=10 days after admission
        days_since_admission_to_first_or = self._calculate_days_diff(admission_date, first_or_proc_date)
        if days_since_admission_to_first_or is not None and days_since_admission_to_first_or >= 10:
            return "Exclusion", "Denominator Exclusion: First OR procedure occurred 10 or more days after admission"

        # Exclusions (Diagnoses)
        principal_dx_entry = all_diagnoses[0]
        principal_dx_code = principal_dx_entry['code']

        # Exclusion: Principal DX of DEEPVIB or PULMOID
        # FIX: This check needs to be inside the loop for all diagnoses, not just principal_dx_entry.
        # It also needs to check if dx_entry is principal_dx_entry.
        # Original: if dx_entry == principal_dx_entry and (dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('DEEPVIB', set())) or dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PULMOID', set()))):
        # Corrected logic will be applied in the loop below.

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']

            # Exclusion: Principal DX of DEEPVIB or PULMOID
            if dx_entry == principal_dx_entry and (dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('DEEPVIB', set())) or dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PULMOID', set()))):
                return "Exclusion", f"Denominator Exclusion: Principal diagnosis is DVT/PE ({dx_code})"

            # Exclusion: Secondary DX of DEEPVIB or PULMOID POA='Y'
            # This applies to all secondary diagnoses, so check for POA='Y'
            if dx_entry != principal_dx_entry and (dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('DEEPVIB', set())) or dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PULMOID', set()))) and \
               poa_status == 'Y':
                return "Exclusion", f"Denominator Exclusion: DVT/PE diagnosis ({dx_code}) present on admission (POA=Y)"

            # Exclusion: HITD (heparin-induced thrombocytopenia) secondary, any POA
            # The JSON states "any secondary diagnosis", not restricted by POA for exclusion.
            if dx_entry != principal_dx_entry and dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('HITD', set())):
                return "Exclusion", f"Denominator Exclusion: Heparin-induced thrombocytopenia ({dx_code}) present"

            # Exclusion: NEURTRAD (acute brain/spinal injury) any POA (but only if POA=Y for the exclusion, as per JSON)
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('NEURTRAD', set())) and poa_status == 'Y':
                return "Exclusion", f"Denominator Exclusion: Acute brain or spinal injury ({dx_code}) present on admission (POA=Y)"

        # Exclusions (Procedures)
        for proc_entry in all_procedures:
            proc_code = proc_entry['code']
            # Exclusion: ECMOP (extracorporeal membrane oxygenation)
            if proc_code.strip().upper() in set(code.strip().upper() for code in appendix.get('ECMOP', set())):
                return "Exclusion", f"Denominator Exclusion: ECMO procedure present ({proc_code})"

        # Numerator Check: DEEPVIB or PULMOID (secondary, not POA)
        has_dvt_pe = False
        # Start from the first secondary diagnosis (DX1) onwards
        for dx_entry in all_diagnoses[1:]:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']
            if (dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('DEEPVIB', set())) or dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PULMOID', set()))) and \
               (poa_status in ['N', 'U', 'W', None] or pd.isna(poa_status)):
                has_dvt_pe = True
                break

        if has_dvt_pe:
            return "Inclusion", "Inclusion: Perioperative Pulmonary Embolism or Deep Vein Thrombosis (secondary, not POA)"
        else:
            return "Exclusion", "Exclusion: No qualifying perioperative DVT/PE found"

    def evaluate_psi13(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_13: Postoperative Sepsis Rate.

        Denominator: Elective surgical discharges (ATYPE=3) for patients >=18 years with OR procedures.
        Numerator: Postoperative sepsis (SEPTI2D secondary, not POA).
        Exclusions: Principal sepsis/infection, secondary sepsis/infection POA='Y',
                    first OR procedure >=10 days after admission.
        Risk Adjustment: Based on immune function severity.
        """
        # Denominator Inclusion: Surgical DRG (Age >=18 handled by base exclusions)
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())):
            return "Exclusion", "Denominator Exclusion: Not a surgical MS-DRG"

        admission_type = row.get('ATYPE')
        if pd.isna(admission_type) or int(admission_type) != 3:
            return "Exclusion", "Denominator Exclusion: Admission not elective (ATYPE != 3)"

        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        admission_date, _ = self._get_admission_discharge_dates(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"


        # Denominator Inclusion: At least one OR procedure
        first_or_proc_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ORPROC')
        if pd.isna(first_or_proc_date):
            return "Exclusion", "Denominator Exclusion: No qualifying OR procedure found"

        # Exclusions (Timing-based)
        # First OR procedure >=10 days after admission
        days_since_admission_to_first_or = self._calculate_days_diff(admission_date, first_or_proc_date)
        if days_since_admission_to_first_or is not None and days_since_admission_to_first_or >= 10:
            return "Exclusion", "Denominator Exclusion: First OR procedure occurred 10 or more days after admission"

        # Exclusions (Diagnoses)
        principal_dx_entry = all_diagnoses[0]
        principal_dx_code = principal_dx_entry['code']

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']

            # Exclusion: Principal DX of SEPTI2D or INFECID
            if dx_entry == principal_dx_entry and \
               (dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('SEPTI2D', set())) or dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('INFECID', set()))):
                return "Exclusion", f"Denominator Exclusion: Principal diagnosis is sepsis or infection ({dx_code})"

            # Exclusion: Secondary DX of SEPTI2D or INFECID POA='Y'
            # This applies to all secondary diagnoses, so check for POA='Y'
            if dx_entry != principal_dx_entry and \
               (dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('SEPTI2D', set())) or dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('INFECID', set()))) and \
               poa_status == 'Y':
                return "Exclusion", f"Denominator Exclusion: Sepsis or infection diagnosis ({dx_code}) present on admission (POA=Y)"

        # Numerator Check: SEPTI2D (postoperative sepsis) secondary, not POA
        has_postop_sepsis = False
        # Start from the first secondary diagnosis (DX1) onwards
        for dx_entry in all_diagnoses[1:]:
            if dx_entry['code'] in appendix.get('SEPTI2D', set()) and \
               (dx_entry['poa'] in ['N', 'U', 'W', None] or pd.isna(dx_entry['poa'])):
                has_postop_sepsis = True
                break

        if has_postop_sepsis:
            risk_category = self._assign_psi13_risk_category(all_diagnoses, all_procedures)
            return "Inclusion", f"Inclusion: Postoperative sepsis (secondary, not POA) - Risk Category: {risk_category}"
        else:
            return "Exclusion", "Exclusion: No qualifying postoperative sepsis found"

    def evaluate_psi14(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_14: Postoperative Wound Dehiscence Rate.

        Denominator: Discharges for patients ages 18 years and older with abdominopelvic surgery
                     (open or non-open approach).
        Numerator: Postoperative reclosure procedures involving the abdominal wall (RECLOIP)
                   AND diagnosis of disruption of internal operation (surgical) wound (ABWALLCD secondary, not POA).
                   Reclosure procedure must occur AFTER the initial abdominopelvic surgery.
        Exclusions:
            - Last RECLOIP occurs on or before first abdominopelvic surgery (open or non-open).
            - Principal DX of ABWALLCD or Secondary DX of ABWALLCD POA='Y'.
            - Length of stay less than 2 days.
            - Obstetric/neonatal discharges.
        Stratification: open_approach (priority 1) vs non_open_approach (priority 2).
        """
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: At least one abdominopelvic procedure (ABDOMIPOPEN or ABDOMIPOTHER)
        has_abdominopelvic_proc = False
        first_abdomip_open_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ABDOMIPOPEN')
        first_abdomip_other_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ABDOMIPOTHER')

        if pd.notna(first_abdomip_open_date) or pd.notna(first_abdomip_other_date):
            has_abdominopelvic_proc = True

        if not has_abdominopelvic_proc:
            return "Exclusion", "Denominator Exclusion: No qualifying abdominopelvic procedure found"

        # Determine the earliest abdominopelvic procedure date for timing comparisons
        initial_abdomip_proc_date = pd.NaT
        if pd.notna(first_abdomip_open_date) and pd.notna(first_abdomip_other_date):
            initial_abdomip_proc_date = min(first_abdomip_open_date, first_abdomip_other_date)
        elif pd.notna(first_abdomip_open_date):
            initial_abdomip_proc_date = first_abdomip_open_date
        elif pd.notna(first_abdomip_other_date):
            initial_abdomip_proc_date = first_abdomip_other_date

        if pd.isna(initial_abdomip_proc_date):
            return "Exclusion", "Data Exclusion: Missing date for initial abdominopelvic procedure"

        # Denominator Inclusion: Length of Stay >= 2 days
        los = row.get('Length_of_stay')
        if pd.isna(los) or int(los) < 2:
            return "Exclusion", "Denominator Exclusion: Length of stay less than 2 days or missing"

        # Exclusions (Procedure Timing)
        last_recloip_date = self._get_latest_procedure_date_by_code_set(all_procedures, 'RECLOIP')

        # If RECLOIP exists, check its timing relative to the initial abdominopelvic procedure
        if pd.notna(last_recloip_date) and last_recloip_date <= initial_abdomip_proc_date:
            return "Exclusion", "Denominator Exclusion: Reclosure procedure occurred on or before initial abdominopelvic surgery"

        # Exclusions (Clinical Condition - Diagnoses)
        principal_dx_entry = all_diagnoses[0]
        principal_dx_code = principal_dx_entry['code']

        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            poa_status = dx_entry['poa']

            # Exclusion: Principal DX of ABWALLCD or Secondary DX of ABWALLCD POA='Y'
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('ABWALLCD', set())):
                if (dx_entry == principal_dx_entry) or (poa_status == 'Y'):
                    return "Exclusion", f"Denominator Exclusion: Wound dehiscence diagnosis ({dx_code}) present on admission or as principal diagnosis"

        # Numerator Logic: RECLOIP procedure AND ABWALLCD diagnosis (secondary, not POA)
        has_recloip_proc = any(p['code'] in appendix.get('RECLOIP', set()) for p in all_procedures)

        has_abwallcd_dx = False
        for dx_entry in all_diagnoses[1:]: # Secondary diagnoses
            if dx_entry['code'] in appendix.get('ABWALLCD', set()) and (dx_entry['poa'] in ['N', 'U', 'W', None] or pd.isna(dx_entry['poa'])):
                has_abwallcd_dx = True
                break

        if has_recloip_proc and has_abwallcd_dx:
            # Final check for numerator: RECLOIP must be *after* initial abdominopelvic surgery
            # If we reached here, it means last_recloip_date > initial_abdomip_proc_date (or last_recloip_date is NaT, but then has_recloip_proc would be false)

            # Assign stratum here, as it's part of the numerator reporting
            stratum = self._assign_psi14_stratum(all_procedures, all_diagnoses)
            return "Inclusion", f"Inclusion: Postoperative wound dehiscence - Stratum: {stratum}"
        else:
            return "Exclusion", "Exclusion: No qualifying postoperative wound dehiscence found"

    def evaluate_psi15(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_15: Abdominopelvic Accidental Puncture or Laceration Rate.

        Denominator: Medical or Surgical DRG (MEDIC2R/SURGI2R), Age >=18, and at least one
                     Abdominopelvic procedure (ABDOMI15P).
        Numerator (Triple Criteria):
            1. Organ-specific injury diagnosis (secondary, not POA)
            2. Related procedure for SAME organ system
            3. Related procedure occurs 1-30 days after the first ABDOMI15P (index procedure)

        Exclusions:
            - Principal DX of accidental puncture/laceration.
            - Secondary DX of accidental puncture/laceration POA='Y' AND a matching related procedure for the same organ.
            - Missing index abdominopelvic procedure date.
        """
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: Medical or Surgical DRG (Age >=18 handled by base exclusions)
        drg = str(row.get('MS-DRG')).zfill(3)
        if drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('SURGI2R', set())) and drg.strip().upper() not in set(code.strip().upper() for code in appendix.get('MEDIC2R', set())):
            return "Exclusion", "Denominator Exclusion: Not a surgical or medical MS-DRG"

        # Denominator Inclusion: At least one abdominopelvic procedure (ABDOMI15P)
        first_abdomi_proc_date = self._get_first_procedure_date_by_code_set(all_procedures, 'ABDOMI15P')
        if pd.isna(first_abdomi_proc_date):
            return "Exclusion", "Denominator Exclusion: No qualifying abdominopelvic procedure (ABDOMI15P) or missing date"

        # Exclusion: Principal diagnosis of accidental puncture/laceration
        # Check if Pdx (principal diagnosis) is in any of the PSI_15 injury diagnosis code sets
        principal_dx_code = all_diagnoses[0]['code']
        if principal_dx_code:
            # Check against the consolidated set of all PSI_15 injury DX codes
            if principal_dx_code in self.all_psi15_injury_dx_codes:
                return "Exclusion", f"Denominator Exclusion: Principal diagnosis is accidental puncture/laceration ({principal_dx_code})"

        # --- Numerator Logic ---
        # Initialize flag to track if any qualifying case is found
        is_numerator_case = False

        # Iterate through secondary diagnoses to find potential injury candidates
        for injury_dx_entry in all_diagnoses[1:]: # Start from DX1 onwards
            injury_dx_code = injury_dx_entry['code']
            injury_poa_status = injury_dx_entry['poa']

            # 1. Check if it's a recognized PSI_15 injury diagnosis
            injury_organ_system = self._get_organ_system_from_code(injury_dx_code, is_dx=True)
            if not injury_organ_system:
                continue # Not a PSI_15 specific injury, move to next diagnosis

            # Get the procedure code set name corresponding to this injury's organ system
            matching_proc_code_set_name = self.organ_system_mappings[injury_organ_system]['proc_codes']

            # 2. Complex POA Exclusion Check (Applies to this specific injury_dx_entry)
            # Exclude if secondary DX is POA='Y' AND there's a matching related procedure for the same organ in the time window.
            if injury_poa_status == 'Y':
                # Check if a matching procedure exists within the 1-30 day window for this POA injury
                if self._check_procedure_timing(all_procedures, first_abdomi_proc_date, matching_proc_code_set_name, min_days=1, max_days=30, inclusive_min=True, inclusive_max=True):
                    # This specific POA injury, with a matching procedure, excludes the entire patient.
                    # This is the "without a principal ICD-10-CM diagnosis code (or secondary diagnosis present on admission)
                    # ... that matches the organ or structure of the potentially related subsequent procedure" logic.
                    return "Exclusion", f"Denominator Exclusion: POA accidental puncture/laceration ({injury_dx_code}) with matching related procedure in time window"
                else:
                    # If POA='Y' but no matching procedure in window, this specific POA injury
                    # does not trigger the complex exclusion, AND it cannot be a numerator case.
                    # So, we just continue to the next diagnosis.
                    continue

            # 3. Numerator Inclusion Check (only if not excluded by complex POA rule above)
            # Diagnosis must NOT be POA ('N', 'U', 'W', None, or NaN)
            if injury_poa_status in ['N', 'U', 'W', None] or pd.isna(injury_poa_status):
                # Check if a matching related procedure exists for the same organ system within the 1-30 day window
                if self._check_procedure_timing(all_procedures, first_abdomi_proc_date, matching_proc_code_set_name, min_days=1, max_days=30, inclusive_min=True, inclusive_max=True):
                    # If both conditions (non-POA injury + matching procedure in window) are met,
                    # then this patient qualifies for the numerator.
                    is_numerator_case = True
                    break # Found one qualifying case, no need to check other diagnoses

        # Final decision based on whether any qualifying case was found
        if is_numerator_case:
            risk_category = self._assign_psi15_risk_category(all_procedures, first_abdomi_proc_date)
            return "Inclusion", f"Inclusion: Abdominopelvic accidental puncture/laceration - Risk Category: {risk_category}"
        else:
            return "Exclusion", "Exclusion: No qualifying abdominopelvic accidental puncture/laceration found"

    def evaluate_psi17(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_17: Birth Trauma Rate – Injury to Neonate.

        Denominator: All newborn discharges (LIVEBND codes).
        Numerator: BIRTHID codes (any position).
        Exclusions: PRETEID (<2000g) (any), OSTEOID (any), MDC14PRINDX (principal).
        """
        all_diagnoses = self._get_all_diagnoses(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: Newborn Population (handled by base exclusions for population_type 'newborn_only')
        # Additional check for LIVEBND codes.
        principal_dx_code = all_diagnoses[0]['code'] if all_diagnoses else None
        if not (principal_dx_code and principal_dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('LIVEBND', set()))):
             return "Exclusion", "Denominator Exclusion: Not a newborn discharge (Principal DX not in LIVEBND codes)"


        # Exclusions (Clinical)
        for dx_entry in all_diagnoses:
            dx_code = dx_entry['code']
            # Exclusion: PRETEID (preterm infant <2000g) any position
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('PRETEID', set())):
                return "Exclusion", f"Denominator Exclusion: Preterm infant with birth weight < 2000g ({dx_code})"
            # Exclusion: OSTEOID (osteogenesis imperfecta) any position
            if dx_code.strip().upper() in set(code.strip().upper() for code in appendix.get('OSTEOID', set())):
                return "Exclusion", f"Denominator Exclusion: Osteogenesis imperfecta diagnosis present ({dx_code})"

        # Numerator Check: BIRTHID (birth trauma injury) any position
        has_birth_trauma = False
        for dx_entry in all_diagnoses:
            if dx_entry['code'] in appendix.get('BIRTHID', set()):
                has_birth_trauma = True
                break

        if has_birth_trauma:
            return "Inclusion", "Inclusion: Birth trauma injury to neonate"
        else:
            return "Exclusion", "Exclusion: No qualifying birth trauma injury found"

    def evaluate_psi18(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_18: Obstetric Trauma Rate – Vaginal Delivery With Instrument.

        Denominator: Instrument-assisted vaginal deliveries (DELOCMD, VAGDELP, INSTRIP).
        Numerator: OBTRAID codes (any position).
        Exclusions: MDC15PRINDX (principal).
        """
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: Delivery Outcome Diagnosis (DELOCMD)
        has_delivery_outcome_dx = any(dx_entry['code'] in appendix.get('DELOCMD', set()) for dx_entry in all_diagnoses)
        if not has_delivery_outcome_dx:
            return "Exclusion", "Denominator Exclusion: No delivery outcome diagnosis found"

        # Denominator Inclusion: Vaginal Delivery Procedure (VAGDELP)
        has_vaginal_delivery_proc = any(proc_entry['code'] in appendix.get('VAGDELP', set()) for proc_entry in all_procedures)
        if not has_vaginal_delivery_proc:
            return "Exclusion", "Denominator Exclusion: No vaginal delivery procedure found"

        # Denominator Inclusion: Instrument-Assisted Delivery Procedure (INSTRIP)
        has_instrument_assisted_proc = any(proc_entry['code'] in appendix.get('INSTRIP', set()) for proc_entry in all_procedures)
        if not has_instrument_assisted_proc:
            return "Exclusion", "Denominator Exclusion: No instrument-assisted delivery procedure found"

        # Numerator Check: OBTRAID (third or fourth degree obstetric injury) any position
        has_obstetric_trauma = False
        for dx_entry in all_diagnoses:
            if dx_entry['code'] in appendix.get('OBTRAID', set()):
                has_obstetric_trauma = True
                break

        if has_obstetric_trauma:
            return "Inclusion", "Inclusion: Obstetric trauma (third or fourth degree) with instrument-assisted vaginal delivery"
        else:
            return "Exclusion", "Exclusion: No qualifying obstetric trauma found for instrument-assisted vaginal delivery"

    def evaluate_psi19(self, row: pd.Series, appendix: Dict[str, Set[str]]) -> Tuple[str, str]:
        """
        Evaluates a patient encounter for PSI_19: Obstetric Trauma Rate – Vaginal Delivery Without Instrument.

        Denominator: Spontaneous vaginal deliveries (DELOCMD, VAGDELP, NOT INSTRIP).
        Numerator: OBTRAID codes (any position).
        Exclusions: INSTRIP (any), MDC15PRINDX (principal).
        """
        all_diagnoses = self._get_all_diagnoses(row)
        all_procedures = self._get_all_procedures(row)
        if not all_diagnoses:
            return "Exclusion", "Data Exclusion: No diagnoses found"

        # Denominator Inclusion: Delivery Outcome Diagnosis (DELOCMD)
        has_delivery_outcome_dx = any(dx_entry['code'] in appendix.get('DELOCMD', set()) for dx_entry in all_diagnoses)
        if not has_delivery_outcome_dx:
            return "Exclusion", "Denominator Exclusion: No delivery outcome diagnosis found"

        # Denominator Inclusion: Vaginal Delivery Procedure (VAGDELP)
        has_vaginal_delivery_proc = any(proc_entry['code'] in appendix.get('VAGDELP', set()) for proc_entry in all_procedures)
        if not has_vaginal_delivery_proc:
            return "Exclusion", "Denominator Exclusion: No vaginal delivery procedure found"

        # Denominator Exclusion: Instrument-Assisted Delivery Procedure (INSTRIP)
        has_instrument_assisted_proc = any(proc_entry['code'] in appendix.get('INSTRIP', set()) for proc_entry in all_procedures)
        if has_instrument_assisted_proc:
            return "Exclusion", "Denominator Exclusion: Instrument-assisted delivery procedure found (PSI_19 excludes these)"

        # Numerator Check: OBTRAID (third or fourth degree obstetric injury) any position
        has_obstetric_trauma = False
        for dx_entry in all_diagnoses:
            if dx_entry['code'] in appendix.get('OBTRAID', set()):
                has_obstetric_trauma = True
                break

        if has_obstetric_trauma:
            return "Inclusion", "Inclusion: Obstetric trauma (third or fourth degree) with spontaneous vaginal delivery"
        else:
            return "Exclusion", "Exclusion: No qualifying obstetric trauma found for spontaneous vaginal delivery"

# Main execution block (outside the class)
if __name__ == "__main__":
    import pandas as pd
    import json

    # Load input Excel
    input_excel = "PSI_Master_Input_Template_With_Disposition.xlsx"
    df = pd.read_excel(input_excel)

    # Load appendix code sets
    # Ensure this file exists and contains the necessary code sets
    with open("PSI_Code_Sets.json", "r") as f: # Corrected to use PSI_Code_Sets.json
        appendix = json.load(f)

    # Initialize calculator
    calculator = PSICalculator(codes_source_path="PSI_Code_Sets.json", psi_definitions_path="PSI_02_19_Compiled_Cleaned.json")

    # Prepare output
    output_rows = []

    # Loop through each encounter and evaluate all PSIs
    for idx, row in df.iterrows():
        encounter_id = row.get("EncounterID", f"Row{idx+1}")
        for psi_number in range(2, 20):
            # Skip PSI_16 as it's not in the provided JSON definition
            if psi_number == 16:
                continue
            psi_code = f"PSI_{psi_number:02}"
            status, rationale, _, _ = calculator.evaluate_psi(row, psi_code) # Capture all return values
            output_rows.append({
                "EncounterID": encounter_id,
                "PSI": psi_code,
                "Status": status,
                "Rationale": rationale
            })

    # Export result
    result_df = pd.DataFrame(output_rows)
    result_df.to_excel("PSI_02_19_Output_Result.xlsx", index=False)
    print("✅ Analysis complete. Output saved to PSI_02_19_Output_Result.xlsx")
