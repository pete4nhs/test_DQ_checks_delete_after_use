# cd "C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\"

import streamlit as st
import pandas as pd
import io
import re
#from datetime import datetime
#import os

# ---------------------- Page config (must be first) ----------------------
st.set_page_config(
    page_title="Local Prices Reporting DQ checks",
    page_icon="https://www.england.nhs.uk/wp-content/themes/nhsengland/static/img/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": "This tool is designed to support ICBs/Trusts to check the data quality of their submission for Local Prices."},)

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

def normalise_header(h: str) -> str:
    """
    Normalise incoming CSV headers so both 'FINANCIAL YEAR' and 'FINANCIAL_YEAR' match.
    Also removes BOM/NBSP/zero-width chars, collapses whitespace, and uppercases.
    """
    if h is None:
        return ""
    h = str(h)

    # Remove BOM/odd whitespace
    h = (h.replace("\ufeff", "")
           .replace("\u00a0", " "))  # NBSP to space
    h = re.sub(r"[\u200B-\u200D\uFEFF]", "", h)  # zero-width
    h = h.strip()

    # Treat underscores as spaces
    h = h.replace("_", " ")

    # Collapse any repeated whitespace and uppercase
    h = re.sub(r"\s+", " ", h).strip().upper()
    return h


def normalise_dataframe_headers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply header normalisation to the whole dataframe.
    If two columns collapse to the same normalised name, keep the first and suffix the rest.
    """
    new_cols = []
    seen = {}
    for c in df.columns:
        nc = normalise_header(c)
        if nc in seen:
            seen[nc] += 1
            nc = f"{nc} ({seen[nc]})"
        else:
            seen[nc] = 0
        new_cols.append(nc)

    df = df.copy()
    df.columns = new_cols
    return df


def format_status_for_output(val):
    """
    Format the Status column for display.
    - 'Valid' stays as-is
    - Row lists become 'Invalid rows: [..]'
    - 'More than X invalid' stays as-is (no prefix)
    """
    if isinstance(val, str):
        v = val.strip()

        if v == "Valid":
            return "Valid"

        # Do NOT prefix summary messages
        if v.startswith("More than"):
            return v

        # Other strings (e.g. Error: column not found)
        return v

    # Only lists / indices get the prefix
    if isinstance(val, (list, tuple, pd.Index)):
        return f"Invalid rows: {list(val)}"

    return val


ALLOWED_COMMISSIONED_SERVICE_CATEGORY_CODES = {
    "12", "21", "22", "25", "26","31", "32", "41",
    "51", "55","61","71", "75","81", "85",
    "91", "92", "93","98", "99",}

NON_ACTIVITY_PODS = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}

BLANK_WHEN_NON_ACTIVITY_POD_FIELDS = {
    "ORGANISATION SITE IDENTIFIER (OF TREATMENT)",
    "ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY)",
    "ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY)",
    # add later 'ACTIVITY TREATMENT FUNCTION CODE'
}

BLANK_RULE_NOTE = (
    "Leave this field blank when POINT OF DELIVERY CODE is "
    "ADJUSTMENT, BLOCK, CQUIN, DRUG, DEVICE, or NAOTHER."
)

def non_activity_blank_rule_triggered(df: pd.DataFrame, field_col: str) -> bool:
    """
    Returns True only when:
      - POD is a non-activity value, AND
      - the field is populated (non-empty)
    """
    pod_col = "POINT OF DELIVERY CODE"
    if field_col not in df.columns or pod_col not in df.columns:
        return False

    pod = (
        df[pod_col]
        .astype("string")
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper()
    )

    field_raw = df[field_col].astype("string")
    field_has_value = field_raw.notna() & (field_raw.str.strip() != "")

    return (field_has_value & pod.isin(NON_ACTIVITY_PODS)).any()

# ---------------------- Header ----------------------
st.image('input_data_other/london_logos_n_name.png', width=1050)
st.title("Automated _Local Prices_ Reporting DQ checks")
st.write('')
st.write('The full documentation on how to fill in the report can be found at https://www.england.nhs.uk/publication/local-prices-reporting-specification-technical-detail-specific-data-requirements/.')
# ---------------------- File upload (CSV only) ----------------------
uploaded_lpr = st.file_uploader(
    "📤 **Upload your Local Prices Reporting as a CSV file.**",
    type=["csv"],
    help="Upload your Local Prices Reporting here. Import only the essential tab as a '.csv' file.")

# If file changes, clear previous results so the UI doesn't show stale data
sig = file_signature(uploaded_lpr)
if sig != st.session_state.uploaded_signature:
    st.session_state.uploaded_signature = sig
    st.session_state.final_df = None
    st.session_state.csv_bytes = None
    st.session_state.calc_done = False
    st.session_state.show_preview = False


# ---------------------- Validators  ----------------------

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
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() < 5]
    invalid = pd.concat([invalid, df[df[col].astype(str).str.len() > 9]])
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

# --------------------- ACTIVITY TREATMENT FUNCTION CODE (mandatory where relevant)
def validate_activity_TFC_columns(df):
    req_cols = ['ACTIVITY TREATMENT FUNCTION CODE', 'POINT OF DELIVERY CODE']
    for c in req_cols:
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."
    
    # (Option A) when run locally use this
    #tfc = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\TFC.csv")

    # (Option B) when running in stlite version with github, use this URL version
    tfc_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/TFC.csv")
    tfc = pd.read_csv(tfc_URL)
      
    valid_codes = set(tfc.iloc[:, 0].dropna().astype(str))

    df['ACTIVITY TREATMENT FUNCTION CODE'] = df['ACTIVITY TREATMENT FUNCTION CODE'].astype(str)
    allowed_pod_values = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}
    invalid = df[
        (~df['ACTIVITY TREATMENT FUNCTION CODE'].isin(valid_codes)) &
        (~df['POINT OF DELIVERY CODE'].isin(allowed_pod_values))    ]
    return  list(invalid.index) if not invalid.empty else "Valid"

# --------------------- LOCAL SUB-SPECIALTY CODE (optional)
def validate_local_sub_columns(df):
    col = 'LOCAL SUB-SPECIALTY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 8]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- COMMISSIONING SERIAL NUMBER (optional)
def validate_comm_serial_n_columns(df):
    col = 'COMMISSIONING SERIAL NUMBER'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 6]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- PROVIDER REFERENCE IDENTIFIER (optional)
def validate_provider_ref_identifier_columns(df):
    col = 'PROVIDER REFERENCE IDENTIFIER'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 20]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- NHS SERVICE AGREEMENT LINE NUMBER (optional)
def validate_nhs_service_cat_n_columns(df):
    col = 'NHS SERVICE AGREEMENT LINE NUMBER'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 10]
    return list(invalid.index) if not invalid.empty else "Valid"

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

    # (Option A) when run locally use this
    #del_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\Delegationservices_v38.csv")

    # (Option B) when running in stlite version with github, use this URL version
    del_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/Delegationservices_v38.csv")
    del_df = pd.read_csv(del_URL)

    valid_codes = set(del_df.iloc[:, 0].dropna().astype(str))
    df[col] = df[col].astype(str)
    invalid = df[~df[col].isin(valid_codes)]
    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- POINT OF DELIVERY CODE (mandatory)
def validate_pod_code_columns(df):
    col = 'POINT OF DELIVERY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[~df[col].isna()]
    invalid = invalid[invalid[col].astype(str).str.len() > 10]

    # (Option A) when run locally use this
    #npod = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables/NPOD.csv")

    # (Option B) when running in stlite version with github, use this URL version
    npod_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/NPOD.csv")
    npod = pd.read_csv(npod_URL)
    
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

    # (Option A) when run locally use this
    #hrg = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\HRG.csv")

    # (Option B) when running in stlite version with github, use this URL version
    hrg_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/HRG.csv")
    hrg = pd.read_csv(hrg_URL)

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


# --------------------- LOCAL PRICE (mandatory)
def validate_local_price_columns(df):
    col = 'LOCAL PRICE'

    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    series = df[col]
    s = series.astype(str).str.strip()

    # Must not be empty
    empty_invalid = series.isna() | (s == "")

    # Must be a number (integer or decimal)
    decimal_ok = s.str.fullmatch(r"\d+(\.\d+)?")

    # Total digits (excluding decimal point) ≤ 18
    digit_count_ok = s.str.replace(".", "", regex=False).str.len() <= 18

    numeric_invalid = ~(decimal_ok & digit_count_ok)

    invalid_mask = empty_invalid | numeric_invalid
    invalid_indices = list(series.index[invalid_mask])

    return "Valid" if not invalid_indices else invalid_indices


# ---------------------- FIELD REQUIREMENT MAP ----------------------
REQUIREMENT_MAP = {
    'FINANCIAL YEAR': 'mandatory',
    'DATE AND TIME DATA SET CREATED': 'mandatory',
    'ORGANISATION IDENTIFIER (CODE OF PROVIDER)': 'mandatory',
    'ORGANISATION SITE IDENTIFIER (OF TREATMENT)': 'mandatory where relevant',
    'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)': 'mandatory',
    'ACTIVITY TREATMENT FUNCTION CODE': 'mandatory where relevant',
    'LOCAL SUB-SPECIALTY CODE': 'optional',
    'COMMISSIONING SERIAL NUMBER': 'optional',
    'PROVIDER REFERENCE IDENTIFIER': 'optional',
    'NHS SERVICE AGREEMENT LINE NUMBER': 'optional',
    'COMMISSIONED SERVICE CATEGORY CODE': 'mandatory',
    'SERVICE CODE': 'mandatory where relevant',
    'POINT OF DELIVERY CODE': 'mandatory',
    'POINT OF DELIVERY FURTHER DETAIL CODE': 'mandatory where relevant',
    'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION': 'mandatory where relevant',
    'LOCAL POINT OF DELIVERY CODE': 'optional',
    'LOCAL POINT OF DELIVERY DESCRIPTION': 'optional',
    'TARIFF CODE': 'mandatory where relevant',
    'LOCAL PRICE': 'mandatory',}

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
                    dtype="string",          # read everything safely as string
                    encoding="utf-8-sig"
                )

                df = df.dropna(how="all").copy()

                # Normalise headers so underscores/spaces/case differences don't matter
                df = normalise_dataframe_headers(df)

                # Clean month/year values (before validation) - now using canonical names
                if "FINANCIAL YEAR" in df.columns:
                    df["FINANCIAL YEAR"] = clean_numeric_text(df["FINANCIAL YEAR"])

                # Build results
                columns = pd.Series([
                    'FINANCIAL YEAR', 'DATE AND TIME DATA SET CREATED',
                    'ORGANISATION IDENTIFIER (CODE OF PROVIDER)',
                    'ORGANISATION SITE IDENTIFIER (OF TREATMENT)',
                    'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)',
                    'ACTIVITY TREATMENT FUNCTION CODE', 'LOCAL SUB-SPECIALTY CODE',
                    'COMMISSIONING SERIAL NUMBER', 'PROVIDER REFERENCE IDENTIFIER',
                    'NHS SERVICE AGREEMENT LINE NUMBER',
                    'COMMISSIONED SERVICE CATEGORY CODE', 'SERVICE CODE',
                    'POINT OF DELIVERY CODE', 'POINT OF DELIVERY FURTHER DETAIL CODE',
                    'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION',
                    'LOCAL POINT OF DELIVERY CODE', 'LOCAL POINT OF DELIVERY DESCRIPTION',
                    'TARIFF CODE', 'LOCAL PRICE'
                ], name='Column name')

                requirement = columns.map(REQUIREMENT_MAP).rename("Field requirement")

                status = pd.Series([
                    format_status_for_output(to_1_based_indices(validate_year_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_datetime_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_cop_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_of_treatment_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_commissioner_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_activity_TFC_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_local_sub_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_comm_serial_n_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_provider_ref_identifier_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_nhs_service_cat_n_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_commissioned_service_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_service_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_pod_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_pod_further_detail_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_pod_further_detail_desc_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_local_pod_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_local_pod_desc_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_tariff_code_columns(df))),
                    format_status_for_output(to_1_based_indices(validate_local_price_columns(df))),
                ], name="Status")


                notes = columns.map(
                    lambda c: BLANK_RULE_NOTE
                    if (c in BLANK_WHEN_NON_ACTIVITY_POD_FIELDS and non_activity_blank_rule_triggered(df, c))
                    else ""
                ).rename("Notes")

                dfs = [columns, requirement, status]

                # Only include Notes if at least one note is populated
                if notes.str.strip().ne("").any():
                    dfs.append(notes)

                final_df = pd.concat([columns, requirement, status], axis=1)

                # Save for preview/download
                csv = final_df.to_csv(index=False)
                st.session_state.csv_bytes = csv.encode("utf-8")
                st.session_state.final_df = final_df
                st.session_state.calc_done = True
                st.session_state.show_preview = False  # do not auto-open
        except Exception as e:
            st.error(f"Something went wrong while reading the file or running checks: {e}")

# ---------------------- Results card (only after Run checks) ----------------------
if st.session_state.calc_done and st.session_state.final_df is not None:
    st.subheader("Results")

    with st.container(border=True):
        st.markdown(
            """
            <div style="font-size:1.05rem; font-weight:600; line-height:1.3;">
                Local Prices Reporting DQ results
            </div>
            """,
            unsafe_allow_html=True,)
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
                file_name="Analysed Local Prices DQ checks.csv",
                mime="text/csv",
                key="dq_download_btn",
                use_container_width=True)

    # Inline preview that persists across reruns (only Status column coloured)
    if st.session_state.show_preview:
        with st.container(border=True):
            st.markdown("**This table shows which columns in your Local Prices Reporting are valid. If data is invalid, the Status column lists the row numbers with incorrect formatting** (if less than 100 records).")
            styled = style_results_table(st.session_state.final_df)
            st.dataframe(
                styled,
                use_container_width=True,
                height=560,
                hide_index=True)
            st.button("Close preview", key="close_preview_btn", on_click=lambda: st.session_state.update(show_preview=False))

else:
    st.info("Please upload a CSV or Excel file and click Run checks.")


# ---------------------- Important note (always visible at the bottom) ----------------------
st.write('')
st.write('')
st.warning("**Please note that uploading and processing DQ checks through this tool does not constitute data submission. " \
"This tool is solely intended to assess the formatting of your file.**")
