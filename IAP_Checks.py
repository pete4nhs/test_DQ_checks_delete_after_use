# cd "C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\"

import streamlit as st
import pandas as pd
import io
from datetime import datetime
#import os

# ---------------------- Page config (must be first) ----------------------

st.set_page_config(
    page_title="IAP DQ checks",
    page_icon="https://www.england.nhs.uk/wp-content/themes/nhsengland/static/img/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": (
            "This tool is designed to support ICBs/Trusts to check the data quality "
            "of their submission for Indicative Activity Plans (IAP).")},)

# ---------------------- Session state initialisation ----------------------

if "final_df" not in st.session_state:
    st.session_state.final_df = None
if "csv_bytes" not in st.session_state:
    st.session_state.csv_bytes = None
if "calc_done" not in st.session_state:
    st.session_state.calc_done = False
if "show_preview" not in st.session_state:
    st.session_state.show_preview = False
if "uploaded_signature" not in st.session_state:
    st.session_state.uploaded_signature = None  # to detect file changes


# ---------------------- Helpers ----------------------

def file_signature(uploaded_file):
    """Create a simple signature of the uploaded CSV file to detect changes."""
    if uploaded_file is None:
        return None
    return (uploaded_file.name, uploaded_file.size)


def to_1_based_indices(result, limit=100):
    """
    Convert 0-based pandas indices to Excel-style row numbers:
    +1 for 1-based indexing, +1 for header row => +2 total.
    """
    if isinstance(result, str):
        return result

    if isinstance(result, (list, tuple, pd.Index)):
        uniq = sorted(set(int(i) for i in result))
        rows = [i + 2 for i in uniq]
        return f"More than {limit} invalid" if len(rows) > limit else rows

    if isinstance(result, pd.DataFrame):
        uniq = sorted(set(int(i) for i in result.index))
        rows = [i + 2 for i in uniq]
        return f"More than {limit} invalid" if len(rows) > limit else rows

    return "Unexpected error"


def clean_numeric_text(s: pd.Series) -> pd.Series:
    return (
        s.astype("string")
         .str.replace("\ufeff", "", regex=False)
         .str.replace("\u00a0", "", regex=False)
         .str.replace(r"[\u200B-\u200D\uFEFF]", "", regex=True)
         .str.strip()
         .str.replace(r"\.0+$", "", regex=True))  # strip trailing .0/.00...


ALLOWED_COMMISSIONED_SERVICE_CATEGORY_CODES = {
    "12", "21", "22", "25", "26","31", "32", "41",
    "51", "55","61","71", "75","81", "85",
    "91", "92", "93","98", "99",}



# ---------------------- Header ----------------------

st.image("input_data_other/london_logos_n_name.png", width=1050)
st.title("Automated _Indicative Activity Plans (IAP)_ Reporting DQ checks")
st.write("")
st.write(
    "The full documentation on how to fill in the report can be found at "
    "[https://www.england.nhs.uk/publication/iap-reporting-specification-technical-detail-specific-data-requirements/]"
    "(https://www.england.nhs.uk/publication/iap-reporting-specification-technical-detail-specific-data-requirements/)")

# ---------------------- File upload (CSV only) ----------------------

uploaded_lpr = st.file_uploader(
    "📤 **Upload your IAP as a CSV file.**",
    type=["csv"],
    help="Upload your IAP here. Import only the essential tab as a '.csv' file.",)

# ---------------------- Reset state if file changes ----------------------

sig = file_signature(uploaded_lpr)
if sig != st.session_state.uploaded_signature:
    st.session_state.uploaded_signature = sig
    st.session_state.final_df = None
    st.session_state.csv_bytes = None
    st.session_state.calc_done = False
    st.session_state.show_preview = False


# ---------------------- Validators ----------------------

# --------------------- FINANCIAL MONTH (mandatory)
def validate_month_columns(df):
    col = 'FINANCIAL MONTH'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].isna() |
        (~pd.to_numeric(df[col], errors="coerce").between(1, 13))]

    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- FINANCIAL YEAR (mandatory)
def validate_year_columns(df):
    col = "FINANCIAL YEAR"
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    # Clean + coerce
    s = df[col].astype(str).str.strip()
    yr = pd.to_numeric(s, errors="coerce")

    invalid = df[
        yr.isna() | (yr < 201011) | (yr > 205051)]

    return list(invalid.index) if not invalid.empty else "Valid"



# --------------------- DATE AND TIME DATA SET CREATED (mandatory)
def validate_datetime_columns(df):
    col = 'DATE AND TIME DATA SET CREATED'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

        df[col] = clean_numeric_text(df[col])
    parsed = pd.to_datetime(df[col], errors="coerce")
    invalid = df[
        df[col].notna() & (
            parsed.isna() |        # not a datetime at all
            parsed.dt.second.isna())]  # seconds missing
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ORGANISATION IDENTIFIER (CODE OF PROVIDER) (mandatory)
def validate_cop_columns(df):
    col = 'ORGANISATION IDENTIFIER (CODE OF PROVIDER)'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    series = df[col].astype(str)
    invalid = df[
        df[col].isna() |
        (series.str.len() < 3) |
        (series.str.len() > 6) |
        series.str.endswith("00", na=False)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ORGANISATION SITE IDENTIFIER (OF TREATMENT) (mandatory where relevant)
def validate_of_treatment_columns(df):
    col = 'ORGANISATION SITE IDENTIFIER (OF TREATMENT)'
    pod_col = 'POINT OF DELIVERY CODE'

    # Ensure required columns exist
    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    values = df[col].astype(str).str.strip()

    # POD values where site identifier must be blank
    pod = df[pod_col].astype(str).str.strip().str.upper()
    non_activity_pods = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}

    # Rule 1: if populated, length must be between 5 and 9
    invalid_length = (df[col].notna() &
        ((values.str.len() < 5) | (values.str.len() > 9)))

    # Rule 2: must be blank when POD is non‑activity
    invalid_when_pod_non_activity = (df[col].notna() &
        pod.isin(non_activity_pods))

    invalid = df[invalid_length | invalid_when_pod_non_activity]

    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY) (optional)
def validate_gp_practice_columns(df):
    col = 'ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY)'
    pod_col = 'POINT OF DELIVERY CODE'

    # Ensure required columns exist
    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    # Load ICB reference
    ref_org = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating IAPs_checks\reference_tables\ICB_and_SubICB_Apr2026.csv")

    # ✅ Decide which codes are valid
    # ICB_Code (Qxx)
    icb_codes = (
        ref_org['ICB_Code']
        .dropna()
        .astype(str)
        .str.strip())

    # Organisation_Code (Sub‑ICB / ODS)
    org_codes = (
        ref_org['Organisation_Code']
        .dropna()
        .astype(str)
        .str.strip())

    # ✅ Combine allowed codes
    valid_codes = set(icb_codes).union(set(org_codes))
    # If later you decide only ICB codes are valid, just use:
    # valid_codes = set(icb_codes)

    values = df[col].astype(str).str.strip()

    # POD values where this field must be blank
    pod = df[pod_col].astype(str).str.strip().str.upper()
    non_activity_pods = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}

    # Rule 1: optional field, but if populated must be valid
    invalid_code = (df[col].notna() &
        (~values.isin(valid_codes)))

    # Rule 2: must be blank when POD is non‑activity
    invalid_when_pod_non_activity = (df[col].notna() &
        pod.isin(non_activity_pods))

    invalid = df[invalid_code | invalid_when_pod_non_activity]

    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY) (optional)
def validate_residence_resp_columns(df):
    col = 'ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY)'
    pod_col = 'POINT OF DELIVERY CODE'

    # Ensure required columns exist
    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    # Load ICB reference
    ref_org = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating IAPs_checks\reference_tables\ICB_and_SubICB_Apr2026.csv")

    # ICB Q-codes (QMJ, QMF, etc.)
    icb_codes = (ref_org['ICB_Code']
        .dropna()
        .astype(str)
        .str.strip())

    # Organisation / Sub‑ICB codes (ODS-style)
    org_codes = (ref_org['Organisation_Code']
        .dropna()
        .astype(str)
        .str.strip())

    # ✅ Allowed values (comment out org_codes if you later decide they are not valid)
    valid_codes = set(icb_codes).union(set(org_codes))
    # If only ICB codes should ever be allowed:
    # valid_codes = set(icb_codes)

    values = df[col].astype(str).str.strip()

    # POD values where residence responsibility must be blank
    pod = df[pod_col].astype(str).str.strip().str.upper()
    non_activity_pods = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}

    # Rule 1: optional field, but if populated must be a valid code
    invalid_code = (df[col].notna() &
        (~values.isin(valid_codes)))

    # Rule 2: must be blank when POD is non-activity
    invalid_when_pod_non_activity = (df[col].notna() &
        pod.isin(non_activity_pods))

    invalid = df[invalid_code | invalid_when_pod_non_activity]

    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- ORGANISATION IDENTIFIER (CODE OF COMMISSIONER) (mandatory)
def validate_commissioner_code_columns(df):
    col = 'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[df[col].isna()]
    invalid = pd.concat([invalid, df[df[col].astype(str).str.len() < 3]])
    invalid = pd.concat([invalid, df[df[col].astype(str).str.len() > 5]])
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- GENERAL MEDICAL PRACTICE (PATIENT REGISTRATION) (optional)
def validate_patient_reg_columns(df):
    col = 'GENERAL MEDICAL PRACTICE (PATIENT REGISTRATION)'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() != 6)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- ACTIVITY TREATMENT FUNCTION CODE (mandatory where relevant)
def validate_activity_TFC_columns(df):
    req_cols = ['ACTIVITY TREATMENT FUNCTION CODE', 'POINT OF DELIVERY CODE']
    for c in req_cols:
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."
    tfc_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\TFC.csv")
    valid_codes = set(tfc_df.iloc[:, 0].dropna().astype(str))
    df['ACTIVITY TREATMENT FUNCTION CODE'] = df['ACTIVITY TREATMENT FUNCTION CODE'].astype(str)
    allowed_pod_values = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}
    invalid = df[
        (~df['ACTIVITY TREATMENT FUNCTION CODE'].isin(valid_codes)) &
        (~df['POINT OF DELIVERY CODE'].isin(allowed_pod_values))    ]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL SUB-SPECIALTY CODE (optional)
def validate_local_sub_columns(df):
    col = 'LOCAL SUB-SPECIALTY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 8)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- WARD CODE (mandatory where relevant)
def validate_ward_code_columns(df):

    # Ensure that the column exists in the DataFrame
    if 'WARD CODE' not in df.columns:
        return "Error: 'WARD CODE' column not found in the data."

    # Validate the column values
    invalid_rows = df[~df['WARD CODE'].isna()]
    invalid_rows = invalid_rows[invalid_rows['WARD CODE'].astype(str).str.len() > 12]  

    if not invalid_rows.empty:
        return list(invalid_rows.index)
    else:
        return "Valid"

# --------------------- COMMISSIONED SERVICE CATEGORY CODE (mandatory)

def validate_commissioned_service_code_columns(df):
    col = "COMMISSIONED SERVICE CATEGORY CODE"
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    s = clean_numeric_text(df[col])

    # Mandatory: blank / NA is invalid
    invalid_mask = s.isna() | (s == "")

    # If present, must be exactly 2 digits (digits-only + length rule)
    present = ~invalid_mask
    invalid_mask |= present & ~s.str.fullmatch(r"\d{2}", na=False)

    # If present and format OK, must be one of the allowed codes
    format_ok = present & s.str.fullmatch(r"\d{2}", na=False)
    invalid_mask |= format_ok & ~s.isin(ALLOWED_COMMISSIONED_SERVICE_CATEGORY_CODES)

    invalid = df[invalid_mask]
    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- SERVICE CODE (mandatory where relevant)
def validate_service_code_columns(df):
    col = 'SERVICE CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df['TARIFF CODE'].isna()]
    invalid = invalid[invalid['TARIFF CODE'].astype(str).str.len() > 12]
    del_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\Delegationservices_v38.csv")
    valid_codes = set(del_df.iloc[:, 0].dropna().astype(str))
    df[col] = df[col].astype(str)
    invalid = df[~df[col].isin(valid_codes)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE (mandatory where relevant)
def validate_specialised_mental_health_code_columns(df):

    # Ensure that the column exists in the DataFrame
    if 'SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE' not in df.columns:
        return "Error: 'SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE' column not found in the data."


    # Validate the column values
    invalid_rows = df[~df['SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE'].isna()]
    invalid_rows = invalid_rows[invalid_rows['SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE'].astype(str).str.len() > 50]  

    if not invalid_rows.empty:
        return list(invalid_rows.index)
    else:
        return "Valid"

# --------------------- POINT OF DELIVERY CODE (mandatory)
def validate_pod_code_columns(df):
    col = 'POINT OF DELIVERY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 10]
    npod = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables/NPOD.csv")
    valid_codes = set(npod.iloc[:, 0].dropna().astype(str))
    df[col] = df[col].astype(str)
    invalid = df[~df[col].isin(valid_codes)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- POINT OF DELIVERY FURTHER DETAIL CODE (mandatory where relevant)
def validate_pod_further_detail_code_columns(df):
    col = 'POINT OF DELIVERY FURTHER DETAIL CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[df[col].notna() &
        (df[col].astype(str).str.len() > 10)]
    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- POINT OF DELIVERY FURTHER DETAIL DESCRIPTION (mandatory where relevant)
def validate_pod_further_detail_desc_columns(df):
    col = 'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[df[col].notna() &
        (df[col].astype(str).str.len() > 100)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL POINT OF DELIVERY CODE (optional)
def validate_local_pod_code_columns(df):
    col = 'LOCAL POINT OF DELIVERY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 50)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL POINT OF DELIVERY DESCRIPTION (optional)
def validate_local_pod_desc_columns(df):
    col = 'LOCAL POINT OF DELIVERY DESCRIPTION'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 100)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL CONTRACT CODE (optional)
def validate_local_contract_code_columns(df):
    col = 'LOCAL CONTRACT CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 20)]
    return list(invalid.index) if not invalid.empty else "Valid"



# --------------------- LOCAL CONTRACT CODE DESCRIPTION (optional)
def validate_local_contract_code_desc_columns(df):
    col = 'LOCAL CONTRACT CODE DESCRIPTION'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 100)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL CONTRACT MONITORING CODE (optional)
def validate_local_contract_monitoring_code_columns(df):
    col = 'LOCAL CONTRACT MONITORING CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 30)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL CONTRACT MONITORING DESCRIPTION (optional)
def validate_local_contract_monitoring_desc_columns(df):
    col = 'LOCAL CONTRACT MONITORING DESCRIPTION'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 100)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- CONTRACT MONITORING ADDITIONAL DETAIL (optional)
def validate_contract_monitoring_detail_columns(df):
    col = 'CONTRACT MONITORING ADDITIONAL DETAIL'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 50)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- CONTRACT MONITORING ADDITIONAL DESCRIPTION (optional)
def validate_contract_monitoring_desc_columns(df):
    col = 'CONTRACT MONITORING ADDITIONAL DESCRIPTION'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 100)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- TARIFF CODE (mandatory where relevant)

def validate_tariff_code_columns(df):
    col = 'TARIFF CODE'
    pod_col = 'POINT OF DELIVERY CODE'

    # Ensure required columns exist
    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    # POD values where tariff code is NOT required (but allowed if <= 50 chars)
    exclude_pods = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER", "OTHER"}

    pod_raw = df[pod_col]
    pod = pod_raw.astype(str).str.strip().str.upper()
    pod_known = pod_raw.notna() & (pod != "")

    tariff_raw = df[col]
    tariff = tariff_raw.astype(str).str.strip()
    has_tariff = tariff_raw.notna() & (tariff != "")

    # Load HRG reference codes
    hrg = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\HRG.csv")

    # HRG column name can vary; handle both
    if 'HRG_code' in hrg.columns:
        hrg_col = 'HRG_code'
    elif 'HRG_Code' in hrg.columns:
        hrg_col = 'HRG_Code'
    else:
        # fallback: first column
        hrg_col = hrg.columns[0]

    valid_hrg = hrg[hrg_col].dropna().astype(str).str.strip().str.upper()

    # Build lookup sets by HRG code length for efficient prefix checking
    codes_by_len = {}
    for code in valid_hrg:
        codes_by_len.setdefault(len(code), set()).add(code)

    tariff_up = tariff.str.upper()

    # starts_with_hrg: True if tariff begins with any valid HRG code
    starts_with_hrg = False
    for L, code_set in codes_by_len.items():
        prefix_ok = tariff_up.str[:L].isin(code_set)
        starts_with_hrg = prefix_ok if starts_with_hrg is False else (starts_with_hrg | prefix_ok)

    # Common length rule: if populated, must be <= 50
    invalid_too_long = has_tariff & (tariff.str.len() > 50)

    # Case A: POD is in exclude list => tariff may be blank OR populated (<=50)
    # So: only invalid here is "too long" (handled above)

    # Case B: POD is NOT in exclude list => tariff must be populated and valid
    required = pod_known & (~pod.isin(exclude_pods))

    invalid_missing_when_required = required & (~has_tariff)
    invalid_bad_prefix_when_required = required & has_tariff & (~starts_with_hrg)

    invalid_mask = invalid_too_long | invalid_missing_when_required | invalid_bad_prefix_when_required

    invalid_rows = df[invalid_mask]
    return list(invalid_rows.index) if not invalid_rows.empty else "Valid"


# --------------------- TARIFF CODE INDICATOR (mandatory where relevant)
def validate_tariff_indicator_columns(df):
    col = 'NATIONAL TARIFF INDICATOR'

    # Ensure the column exists
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    # Optional field: empty is allowed; if populated, must be exactly one character: Y or N
    invalid_rows = df[
        df[col].notna() &
        ((df[col].astype(str).str.len() != 1) |
            (~df[col].astype(str).str.upper().isin(['Y', 'N'])))]

    return list(invalid_rows.index) if not invalid_rows.empty else "Valid"


# --------------------- CONTRACT MONITORING PLANNED ACTIVITY (optional)
def validate_contract_monitoring_activity_columns(df):
    act_col = 'CONTRACT MONITORING PLANNED ACTIVITY'
    pod_col = 'POINT OF DELIVERY CODE'

    # Ensure required columns exist
    for col in (act_col, pod_col):
        if col not in df.columns:
            return f"Error: '{col}' column not found in the data."

    # Normalise POD values
    raw_pod = df[pod_col]
    pod = (
        raw_pod.astype(str)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper())

    pod_unknown = (
        raw_pod.isna() |
        pod.isin({
            "", "N/A", "#N/A", "NA", "#NA","NOT KNOWN", "UNKNOWN", 
            "NOT APPLICABLE","NONE", "NULL"}))

    # POD codes where activity must be zero (per spec)
    pod_requires_zero = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}

    must_be_zero = pod.isin(pod_requires_zero) | pod_unknown

    # Normalise activity values
    raw = df[act_col]
    act_str = raw.astype(str).str.strip()

    # Define "has a value" (treat whitespace-only as empty)
    has_value = raw.notna() & (act_str != "")

    # Parse numeric safely
    act_num = pd.to_numeric(act_str.where(has_value), errors="coerce")

    # Format rule (optional field):
    # - if populated, must be numeric with up to 10 digits before decimal
    # - and up to 3 decimal places
    pattern_ok = act_str.where(has_value).str.fullmatch(r"\d+(\.\d{1,3})?")
    int_part_len = act_str.where(has_value).str.split(".", n=1).str[0].str.len()

    format_invalid = has_value & (
        act_num.isna() |
        (~pattern_ok.fillna(False)) |
        (int_part_len > 10) |
        (act_num < 0))

    # Conditional zero rule:
    # when POD requires zero, activity must be explicitly present and equal to 0
    zero_rule_invalid = must_be_zero & (~has_value |
        act_num.isna() |
        (act_num != 0))

    invalid_mask = format_invalid | zero_rule_invalid
    invalid_rows = df[invalid_mask]

    return list(invalid_rows.index) if not invalid_rows.empty else "Valid"


# --------------------- CONTRACT MONITORING PLANNED PRICE (mandatory where relevant)
def validate_contract_monitoring_price_columns(df):
    col = 'CONTRACT MONITORING PLANNED PRICE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 18)]
    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- CONTRACT MONITORING PLANNED MARKET FORCES FACTOR (mandatory where relevant)
def validate_contract_monitoring_market_columns(df):
    col = 'CONTRACT MONITORING PLANNED MARKET FORCES FACTOR'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 18)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- NAME OF SUBMITTER (mandatory)
def validate_name_of_submitter_columns(df):
    col = 'NAME OF SUBMITTER'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].isna() |
        (df[col].astype(str).str.len() > 100)]
    return list(invalid.index) if not invalid.empty else "Valid"

# ---------------------- FIELD REQUIREMENT MAP ----------------------
REQUIREMENT_MAP = {
    'FINANCIAL MONTH': 'mandatory','FINANCIAL YEAR': 'mandatory',
    'DATE AND TIME DATA SET CREATED': 'mandatory',
    'ORGANISATION IDENTIFIER (CODE OF PROVIDER)': 'mandatory',
    'ORGANISATION SITE IDENTIFIER (OF TREATMENT)': 'mandatory where relevant',
    'ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY)': 'optional',
    'ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY)': 'optional',
    'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)': 'mandatory',
    'GENERAL MEDICAL PRACTICE (PATIENT REGISTRATION)': 'optional',
    'ACTIVITY TREATMENT FUNCTION CODE': 'mandatory where relevant',
    'LOCAL SUB-SPECIALTY CODE': 'optional',
    'WARD CODE': 'mandatory where relevant',
    'COMMISSIONED SERVICE CATEGORY CODE': 'mandatory',
    'SERVICE CODE': 'mandatory where relevant',
    'SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE': 'mandatory where relevant',
    'POINT OF DELIVERY CODE': 'mandatory',
    'POINT OF DELIVERY FURTHER DETAIL CODE': 'mandatory where relevant',
    'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION': 'mandatory where relevant',
    'LOCAL POINT OF DELIVERY CODE': 'optional',
    'LOCAL POINT OF DELIVERY DESCRIPTION': 'optional',
    'LOCAL CONTRACT CODE': 'optional',
    'LOCAL CONTRACT CODE DESCRIPTION': 'optional',
    'LOCAL CONTRACT MONITORING CODE': 'optional',
    'LOCAL CONTRACT MONITORING DESCRIPTION': 'optional',
    'CONTRACT MONITORING ADDITIONAL DETAIL': 'optional',
    'CONTRACT MONITORING ADDITIONAL DESCRIPTION': 'optional',
    'TARIFF CODE': 'mandatory where relevant',
    'NATIONAL TARIFF INDICATOR': 'mandatory where relevant', 'CONTRACT MONITORING PLANNED ACTIVITY': 'optional',
    'CONTRACT MONITORING PLANNED PRICE': 'mandatory where relevant',
    'CONTRACT MONITORING PLANNED MARKET FORCES FACTOR': 'mandatory where relevant',
    'NAME OF SUBMITTER': 'mandatory'}

# ---------------------- STYLING (only Status column coloured) ----------------------
def style_results_table(df: pd.DataFrame):
    """
    Colour only the 'Status' column:
      - Blue when Status == "Valid"
      - Red when Requirement == "mandatory" and the row is invalid or Empty
      - Black otherwise
    """
    def _style_status_cell(row_slice):
        row_idx = row_slice.name
        req = str(df.loc[row_idx, 'Field requirement']).strip().lower()
        status = df.loc[row_idx, 'Status']

        def is_invalid_or_empty(val):
            if isinstance(val, str):
                return val.strip() != "Valid"
            elif isinstance(val, (list, tuple, pd.Index)):
                return len(val) > 0
            return True

        is_valid = isinstance(status, str) and status.strip() == "Valid"

        if is_valid:
            return ['color: blue']
        elif req == 'mandatory' and is_invalid_or_empty(status):
            return ['color: red']
        else:
            return ['color: black']

    return df.style.apply(_style_status_cell, axis=1, subset=['Status'])


# ---------------------- Run checks button ----------------------

if st.button("Run checks", type="primary"):
    if uploaded_lpr is None:
        st.warning("Please upload a CSV file before running checks.")
    else:
        try:
            with st.spinner("Running calculations..."):
                df = pd.read_csv(
                    uploaded_lpr,
                    dtype={
                        "FINANCIAL MONTH": "string",
                        "FINANCIAL YEAR": "string",
                        "DATE AND TIME DATA SET CREATED": "string",},
                    encoding="utf-8-sig")

                df = df.dropna(how="all").copy()


                # Clean month/year values (before validation)
                df["FINANCIAL MONTH"] = clean_numeric_text(df["FINANCIAL MONTH"])
                df["FINANCIAL YEAR"]  = clean_numeric_text(df["FINANCIAL YEAR"])

                # Build results
                columns = pd.Series([
                'FINANCIAL MONTH', 'FINANCIAL YEAR', 'DATE AND TIME DATA SET CREATED',
                'ORGANISATION IDENTIFIER (CODE OF PROVIDER)',
                'ORGANISATION SITE IDENTIFIER (OF TREATMENT)',
                'ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY)',
                'ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY)',
                'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)',
                'GENERAL MEDICAL PRACTICE (PATIENT REGISTRATION)',
                'ACTIVITY TREATMENT FUNCTION CODE', 'LOCAL SUB-SPECIALTY CODE',
                'WARD CODE', 'COMMISSIONED SERVICE CATEGORY CODE', 'SERVICE CODE',
                'SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE',
                'POINT OF DELIVERY CODE', 'POINT OF DELIVERY FURTHER DETAIL CODE',
                'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION',
                'LOCAL POINT OF DELIVERY CODE', 'LOCAL POINT OF DELIVERY DESCRIPTION',
                'LOCAL CONTRACT CODE', 'LOCAL CONTRACT CODE DESCRIPTION',
                'LOCAL CONTRACT MONITORING CODE', 'LOCAL CONTRACT MONITORING DESCRIPTION',
                'CONTRACT MONITORING ADDITIONAL DETAIL',
                'CONTRACT MONITORING ADDITIONAL DESCRIPTION', 'TARIFF CODE',
                'NATIONAL TARIFF INDICATOR', 'CONTRACT MONITORING PLANNED ACTIVITY',
                'CONTRACT MONITORING PLANNED PRICE',
                'CONTRACT MONITORING PLANNED MARKET FORCES FACTOR',
                'NAME OF SUBMITTER'
                ], name='Column name')

                requirement = columns.map(REQUIREMENT_MAP).rename("Field requirement")

                status = pd.Series([
                to_1_based_indices(validate_month_columns(df)), to_1_based_indices(validate_year_columns(df)), to_1_based_indices(validate_datetime_columns(df))
                  ,to_1_based_indices(validate_cop_columns(df)),to_1_based_indices(validate_of_treatment_columns(df)),to_1_based_indices(validate_gp_practice_columns(df))
                  ,to_1_based_indices(validate_residence_resp_columns(df)),to_1_based_indices(validate_commissioner_code_columns(df)),to_1_based_indices(validate_patient_reg_columns(df))
                  ,to_1_based_indices(validate_activity_TFC_columns(df)), to_1_based_indices(validate_local_sub_columns(df)),to_1_based_indices(validate_ward_code_columns(df))
                  ,to_1_based_indices(validate_commissioned_service_code_columns(df)),to_1_based_indices(validate_service_code_columns(df))
                  ,to_1_based_indices(validate_specialised_mental_health_code_columns(df)),to_1_based_indices(validate_pod_code_columns(df))
                  ,to_1_based_indices(validate_pod_further_detail_code_columns(df)),to_1_based_indices(validate_pod_further_detail_desc_columns(df))
                  ,to_1_based_indices(validate_local_pod_code_columns(df)),to_1_based_indices(validate_local_pod_desc_columns(df)),to_1_based_indices(validate_local_contract_code_columns(df))
                  ,to_1_based_indices(validate_local_contract_code_desc_columns(df)),to_1_based_indices(validate_local_contract_monitoring_code_columns(df))
                  ,to_1_based_indices(validate_local_contract_monitoring_desc_columns(df)),to_1_based_indices(validate_contract_monitoring_detail_columns(df))
                  ,to_1_based_indices(validate_contract_monitoring_desc_columns(df)),to_1_based_indices(validate_tariff_code_columns(df))
                  ,to_1_based_indices(validate_tariff_indicator_columns(df)),to_1_based_indices(validate_contract_monitoring_activity_columns(df))
                  ,to_1_based_indices(validate_contract_monitoring_price_columns(df)),to_1_based_indices(validate_contract_monitoring_market_columns(df))
                  ,to_1_based_indices(validate_name_of_submitter_columns(df))
                ], name="Status")

                final_df = pd.concat([columns, requirement, status], axis=1)

                # Save for preview/download
                csv = final_df.to_csv(index=False)
                st.session_state.csv_bytes = csv.encode("utf-8")
                st.session_state.final_df = final_df
                st.session_state.calc_done = True
                st.session_state.show_preview = False  # do not auto-open

            st.success("IAP CSV uploaded successfully!")
        except Exception as e:
            st.error(f"Failed to read CSV file. {e}")


# ---------------------- Results ----------------------

if st.session_state.calc_done and st.session_state.final_df is not None:
    st.subheader("Results")

    with st.container(border=True):
        st.markdown(
            """
            <div style="font-size:1.05rem; font-weight:600; line-height:1.3;">
                IAP DQ results
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("Preview or download the analysed results")

        # Two half-width buttons
        col1, col2 = st.columns([1, 1], vertical_alignment="top")
        with col1:
            if st.button("👁️ View results", key="view_results_btn", use_container_width=True):
                st.session_state.show_preview = True
        with col2:
            st.download_button(
                label="⬇️ Download CSV",
                data=st.session_state.csv_bytes,
                file_name="Analysed IAP DQ checks.csv",
                mime="text/csv",
                key="dq_download_btn",
                use_container_width=True
            )

    # Inline preview that persists across reruns (only Status column coloured)
    if st.session_state.show_preview:
        with st.container(border=True):
            st.markdown("**This table shows which columns in your IAP Reporting are valid. If data is invalid, the Status column lists the row numbers with incorrect formatting** (if less than 100 records).")
            styled = style_results_table(st.session_state.final_df)
            st.dataframe(
                styled,
                use_container_width=True,
                height=560,
                hide_index=True
            )
            st.button("Close preview", key="close_preview_btn", on_click=lambda: st.session_state.update(show_preview=False))

else:
    st.info("Please upload a CSV file and click Run checks.")


# ---------------------- Important note ----------------------

st.write("")
st.write("")
st.warning(
    "**Please note that uploading and processing DQ checks through this tool does not "
    "constitute data submission. This tool is solely intended to assess the formatting "
    "of your file.**")