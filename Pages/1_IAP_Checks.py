# cd "C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\stlite_version\"

import streamlit as st
import pandas as pd
import re

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
if "show_instruction_msg" not in st.session_state:
    st.session_state.show_instruction_msg = True
if "upload_success" not in st.session_state:
    st.session_state.upload_success = False


# ---------------------- Helpers ----------------------

def file_signature(uploaded_file):
    """Create a simple signature of the uploaded CSV file to detect changes."""
    if uploaded_file is None:
        return None
    return (uploaded_file.name, uploaded_file.size)

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

NON_ACTIVITY_PODS = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}

# Your extra exemption list goes here
OTHER_BLANK_ALLOWED_PODS = set()

BLANK_ALLOWED_PODS = NON_ACTIVITY_PODS.union(OTHER_BLANK_ALLOWED_PODS)

def get_clean_and_blank(df, col):
    cleaned = df[col].fillna("").astype("string").str.strip()
    blank = cleaned.eq("")
    return cleaned, blank

def normalise_header(h: str) -> str:
    """
    Standardise a column name so that:
    - Underscores are treated as spaces
    - Case differences are removed (converted to uppercase)
    - Hidden Excel characters are removed
    - Extra spaces inside the string are NOT corrected (strict mode)
    """
    if h is None:
        return ""

    h = str(h)

    # Remove hidden / problematic characters from Excel exports
    h = (h.replace("\ufeff", "")   # BOM
         .replace("\u00a0", " "))  # NBSP → normal space
    
    # Remove zero-width characters
    h = re.sub(r"[\u200B-\u200D\uFEFF]", "", h)

    # Trim leading/trailing spaces ONLY (keep internal spacing strict)
    h = h.strip()

    # Treat underscores as spaces
    h = h.replace("_", " ")

    # Convert to uppercase (final step)
    h = h.upper()

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


def normalise_invalid_value_for_status(val):
    """
    Converts invalid values into a readable form for the Status column.
    Blank or missing values are shown clearly as '(blank)'.
    """
    if pd.isna(val):
        return "(blank)"

    val = str(val).strip()

    if val == "":
        return "(blank)"

    return val


def get_invalid_indices(result):
    """
    Extracts the raw 0-based dataframe indices returned by the validator functions.
    """
    if isinstance(result, str):
        return []

    if isinstance(result, pd.DataFrame):
        return list(result.index)

    if isinstance(result, pd.Index):
        return list(result)

    if isinstance(result, (list, tuple)):
        return list(result)

    return []


def get_status_indices_after_suppressing_rule_values(
    df: pd.DataFrame,
    col: str,
    invalid_indices):
    """
    Suppresses values from the Status list where the value itself is technically
    valid, but fails only because of a contextual POD rule.

    Returns:
      - indices to still show in Status
      - count of rows suppressed because the field should be blank
      - count of rows suppressed because the field should be zero
    """
    if len(invalid_indices) == 0:
        return invalid_indices, 0, 0

    pod_col = "POINT OF DELIVERY CODE"

    if col not in df.columns or pod_col not in df.columns:
        return invalid_indices, 0, 0

    idx = pd.Index(invalid_indices)

    pod = (
        df.loc[idx, pod_col]
        .astype("string")
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper())

    value = df.loc[idx, col].astype("string").str.strip()
    has_value = value.notna() & (value != "")

    suppress_from_status = pd.Series(False, index=idx)
    suppressed_blank_count = 0
    suppressed_zero_count = 0

    # ------------------------------------------------------------
    # Rule 1: Fields that should be blank for non-activity PODs
    # ------------------------------------------------------------
    if col in BLANK_WHEN_NON_ACTIVITY_POD_FIELDS:
        blank_rule_issue = pod.isin(NON_ACTIVITY_PODS) & has_value

        otherwise_valid = pd.Series(False, index=idx)

        if col == "ORGANISATION SITE IDENTIFIER (OF TREATMENT)":
            otherwise_valid = has_value & value.str.len().between(5, 9)

        elif col in {
            "ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY)",
            "ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY)"}:
            ref_org_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/ICB_and_SubICB_Apr2026.csv")
            ref_org = pd.read_csv(ref_org_URL)

            icb_codes = ref_org["ICB_Code"].dropna().astype(str).str.strip()
            org_codes = ref_org["Organisation_Code"].dropna().astype(str).str.strip()
            valid_codes = set(icb_codes).union(set(org_codes))

            otherwise_valid = has_value & value.isin(valid_codes)

        elif col == "ACTIVITY TREATMENT FUNCTION CODE":
            tfc_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/TFC.csv")
            tfc_df = pd.read_csv(tfc_URL)

            valid_codes = set(
                tfc_df.iloc[:, 0]
                .dropna()
                .astype(str)
                .str.strip())

            otherwise_valid = has_value & value.isin(valid_codes)

        suppress_blank = blank_rule_issue & otherwise_valid
        suppress_from_status |= suppress_blank
        suppressed_blank_count += int(suppress_blank.sum())

    # ------------------------------------------------------------
    # Rule 2: TARIFF CODE should be blank for excluded PODs
    # ------------------------------------------------------------
    if col == "TARIFF CODE":
        exclude_pods = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG","DEVICE", "NAOTHER", "OTHER"}

        blank_rule_issue = pod.isin(exclude_pods) & has_value

        hrg_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/HRG.csv")
        hrg = pd.read_csv(hrg_URL)

        if "HRG_code" in hrg.columns:
            hrg_col = "HRG_code"
        elif "HRG_Code" in hrg.columns:
            hrg_col = "HRG_Code"
        else:
            hrg_col = hrg.columns[0]

        valid_hrg = (
            hrg[hrg_col]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper())

        tariff_clean = (
            value
            .str.replace("\ufeff", "", regex=False)
            .str.replace("\xa0", " ", regex=False)
            .str.strip())

        tariff_up = tariff_clean.str.upper()

        codes_by_len = {}
        for code in valid_hrg:
            codes_by_len.setdefault(len(code), set()).add(code)

        starts_with_hrg = pd.Series(False, index=idx)
        for L, code_set in codes_by_len.items():
            starts_with_hrg |= tariff_up.str[:L].isin(code_set)

        otherwise_valid = (
            has_value
            & starts_with_hrg
            & (tariff_clean.str.len() <= 50))

        suppress_blank = blank_rule_issue & otherwise_valid
        suppress_from_status |= suppress_blank
        suppressed_blank_count += int(suppress_blank.sum())

    # ------------------------------------------------------------
    # Rule 3: CONTRACT MONITORING PLANNED ACTIVITY should be zero
    # ------------------------------------------------------------
    if col == "CONTRACT MONITORING PLANNED ACTIVITY":
        raw_pod = df.loc[idx, pod_col].astype("string")

        pod_unknown = (
            raw_pod.isna()
            | pod.isin({
                "", "N/A", "#N/A", "NA", "#NA",
                "NOT KNOWN", "UNKNOWN", "NOT APPLICABLE", "NONE", "NULL"}))

        must_be_zero = pod.isin(NON_ACTIVITY_PODS) | pod_unknown

        act_str = value
        has_act_value = has_value
        act_num = pd.to_numeric(act_str.where(has_act_value), errors="coerce")

        pattern_ok = act_str.where(has_act_value).str.fullmatch(r"\d+(\.\d{1,3})?")
        int_part_len = act_str.where(has_act_value).str.split(".", n=1).str[0].str.len()

        structurally_valid_activity = (
            has_act_value
            & act_num.notna()
            & pattern_ok.fillna(False)
            & (int_part_len <= 10)
            & (act_num >= 0))

        zero_rule_issue = (
            must_be_zero
            & (
                (~has_act_value)
                | structurally_valid_activity)
            & (act_num.fillna(-999999) != 0))

        suppress_zero = zero_rule_issue
        suppress_from_status |= suppress_zero
        suppressed_zero_count += int(suppress_zero.sum())

    indices_to_show = list(idx[~suppress_from_status])

    return indices_to_show, suppressed_blank_count, suppressed_zero_count

def build_status_with_invalid_values(df: pd.DataFrame, col: str, result, limit=100):
    """
    Builds the Status column.

    Returns:
      - 'Valid' if the check passed
      - error message if the validator returned an error
      - 'Invalid rows contain: [...]' with distinct invalid values shown once

    Contextual rule handling:
      - technically valid values that should be blank are counted, not listed
      - structurally valid activity values that should be zero are counted, not listed
      - genuinely invalid values are still listed
    """
    if isinstance(result, str):
        return result

    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    invalid_indices = get_invalid_indices(result)

    if len(invalid_indices) == 0:
        return "Valid"

    invalid_indices_for_status, blank_count, zero_count = (
        get_status_indices_after_suppressing_rule_values(
            df=df,
            col=col,
            invalid_indices=invalid_indices))

    messages = []

    # Case where there are genuinely invalid values to list
    if len(invalid_indices_for_status) > 0:
        invalid_values = (
            df.loc[invalid_indices_for_status, col]
            .map(normalise_invalid_value_for_status)
            .tolist())

        # Keep each invalid value once, preserving first-seen order
        unique_values = list(dict.fromkeys(invalid_values))

        if len(unique_values) > limit:
            visible_values = unique_values[:limit]
            messages.append(
                f"Invalid rows contain: {visible_values} "
                f"plus {len(unique_values) - limit} more unique invalid value(s)")
        else:
            messages.append(f"Invalid rows contain: {unique_values}")

    # Add contextual counts
    if blank_count > 0:
        messages.append(
            f"{blank_count} row(s) should be blank, see Suggestions")

    if zero_count > 0:
        messages.append(
            f"{zero_count} row(s) should be 0, see Suggestions")

    # Case where everything was suppressed into contextual counts
    if len(messages) == 0:
        return "Invalid"

    if len(invalid_indices_for_status) == 0:
        return "Invalid (" + "; ".join(messages) + ")"

    return ". Also ".join(messages)



BLANK_WHEN_NON_ACTIVITY_POD_FIELDS = {
    "ORGANISATION SITE IDENTIFIER (OF TREATMENT)",
    "ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY)",
    "ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY)",
    "ACTIVITY TREATMENT FUNCTION CODE"}

BLANK_RULE_NOTE = (
    "Leave this field blank when POINT OF DELIVERY CODE is "
    "ADJUSTMENT, BLOCK, CQUIN, DRUG, DEVICE, or NAOTHER.")

TARIFF_RULE_NOTE = (
    "Leave this field blank when POINT OF DELIVERY CODE is "
    "ADJUSTMENT, BLOCK, CQUIN, DRUG, DEVICE, NAOTHER, or OTHER. "
    "For all other Point of Delivery Codes, a valid HRG‑based tariff code is required.")

CONTR_MON_PLAN_ACT_RULE_NOTE = (
    "This field must be set to 0 when POINT OF DELIVERY CODE is "
    "ADJUSTMENT, BLOCK, CQUIN, DRUG, DEVICE, or NAOTHER")


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
        .str.upper())

    field_raw = df[field_col].astype("string")
    field_has_value = field_raw.notna() & (field_raw.str.strip() != "")

    return (field_has_value & pod.isin(NON_ACTIVITY_PODS)).any()


def get_tariff_invalid_mask(df: pd.DataFrame) -> pd.Series | None:
    col = "TARIFF CODE"
    pod_col = "POINT OF DELIVERY CODE"

    for c in (col, pod_col):
        if c not in df.columns:
            return None

    exclude_pods = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER", "OTHER"}

    pod_raw = df[pod_col]
    pod = pod_raw.astype("string").str.strip().str.upper()
    pod_known = pod_raw.notna() & (pod != "")

    tariff_raw = df[col]
    tariff = tariff_raw.astype("string").str.strip()

    tariff_clean = (tariff
        .str.replace("\ufeff", "", regex=False)
        .str.replace("\xa0", " ", regex=False)
        .str.strip())

    tariff_up = tariff_clean.str.upper()
    has_tariff = tariff_raw.notna() & (tariff_clean != "")

    # when running locally
#    hrg = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\reference_tables\HRG.csv")

    # when running in stlite
    hrg_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/HRG.csv")
    hrg = pd.read_csv(hrg_URL)

    if "HRG_code" in hrg.columns:
        hrg_col = "HRG_code"
    elif "HRG_Code" in hrg.columns:
        hrg_col = "HRG_Code"
    else:
        hrg_col = hrg.columns[0]

    valid_hrg = hrg[hrg_col].dropna().astype(str).str.strip().str.upper()

    codes_by_len = {}
    for code in valid_hrg:
        codes_by_len.setdefault(len(code), set()).add(code)

    starts_with_hrg = pd.Series(False, index=df.index)
    for L, code_set in codes_by_len.items():
        starts_with_hrg |= tariff_up.str[:L].isin(code_set)

    invalid_too_long = has_tariff & (tariff_clean.str.len() > 50)

    required = pod_known & (~pod.isin(exclude_pods))
    invalid_missing_when_required = required & (~has_tariff)
    invalid_bad_prefix_required = required & has_tariff & (~starts_with_hrg)

    excluded = pod_known & pod.isin(exclude_pods)
    invalid_bad_prefix_excluded = excluded & has_tariff & (~starts_with_hrg)

    return (invalid_too_long
        | invalid_missing_when_required
        | invalid_bad_prefix_required
        | invalid_bad_prefix_excluded)

def tariff_rule_triggered(df: pd.DataFrame) -> bool:
    invalid_mask = get_tariff_invalid_mask(df)
    return False if invalid_mask is None else invalid_mask.any()

def non_activity_zero_rule_triggered(df: pd.DataFrame, field_col: str) -> bool:
    """
    Returns True only when:
      - POD is a non-activity value, AND
      - the field is missing, non-numeric, or not equal to zero
    """
    pod_col = "POINT OF DELIVERY CODE"

    if field_col not in df.columns or pod_col not in df.columns:
        return False

    pod = (df[pod_col]
        .astype("string")
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper())

    raw = df[field_col].astype("string")
    val = raw.str.strip()

    has_value = raw.notna() & (val != "")
    act_num = pd.to_numeric(val.where(has_value), errors="coerce")

    must_be_zero = pod.isin(NON_ACTIVITY_PODS)

    return (must_be_zero & (
            (~has_value) |
            act_num.isna() |
            (act_num != 0))).any()


# ---------------------- Length restriction suggestions ----------------------

LENGTH_RULES = {
    "GENERAL MEDICAL PRACTICE (PATIENT REGISTRATION)": {"type": "exact", "value": 6},
    "LOCAL SUB-SPECIALTY CODE": {"type": "max", "value": 8},
    "WARD CODE": {"type": "max", "value": 12},
    "LOCAL POINT OF DELIVERY CODE": {"type": "max", "value": 50},
    "LOCAL POINT OF DELIVERY DESCRIPTION": {"type": "max", "value": 100},
    "LOCAL CONTRACT CODE": {"type": "max", "value": 20},
    "LOCAL CONTRACT CODE DESCRIPTION": {"type": "max", "value": 100},
    "LOCAL CONTRACT MONITORING CODE": {"type": "max", "value": 30},
    "LOCAL CONTRACT MONITORING DESCRIPTION": {"type": "max", "value": 100},
    "CONTRACT MONITORING ADDITIONAL DETAIL": {"type": "max", "value": 50},
    "CONTRACT MONITORING ADDITIONAL DESCRIPTION": {"type": "max", "value": 100},}

def length_rule_triggered(df: pd.DataFrame, col: str) -> bool:
    """
    Returns True only when the field has an actual character-length issue.
    This avoids showing a length note for unrelated problems such as a missing column.
    """
    if col not in df.columns or col not in LENGTH_RULES:
        return False

    rule = LENGTH_RULES[col]
    s = df[col].astype("string")
    present = s.notna()

    if rule["type"] == "exact":
        return (present & (s.str.len() != rule["value"])).any()

    if rule["type"] == "max":
        return (present & (s.str.len() > rule["value"])).any()

    return False

def get_length_rule_note(col: str) -> str:
    """
    Returns a friendly note describing the character length rule.
    """
    rule = LENGTH_RULES.get(col)
    if not rule:
        return ""

    if rule["type"] == "exact":
        return f"This field must be exactly {rule['value']} characters long."

    if rule["type"] == "max":
        return f"This field must be {rule['value']} characters or fewer."

    return ""



# ---------------------- Header ----------------------

st.image("input_data_other/london_logos_n_name.png", width=1050)
st.title("Automated _Indicative Activity Plans (IAP)_ Reporting DQ checks")
st.write("")
st.write(
    "The full documentation on how to fill in the report can be found at "
    "[https://www.england.nhs.uk/publication/iap-reporting-specification-technical-detail-specific-data-requirements/]"
    "(https://www.england.nhs.uk/publication/iap-reporting-specification-technical-detail-specific-data-requirements/)")


# ---------------------- Instruction message ----------------------
instruction_msg = st.empty()

if st.session_state.show_instruction_msg:
    instruction_msg.info("Please upload a CSV file and click 'Run checks'.")
else:
    instruction_msg.empty()

# ---------------------- File upload (CSV only) ----------------------

uploaded_lpr = st.file_uploader(
    "📤 **Upload your IAP as a CSV file.**",
    type=["csv"],
    help="Upload your IAP here. Import only the essential tab as a '.csv' file.",)

# ---------------------- Reset state if file changes ----------------------

sig = file_signature(uploaded_lpr)

# ---------------------- Handle upload / removal ----------------------

# Case 1: file removed
if uploaded_lpr is None:
    st.session_state.uploaded_signature = None
    st.session_state.upload_success = False
    st.session_state.final_df = None
    st.session_state.csv_bytes = None
    st.session_state.calc_done = False
    st.session_state.show_preview = False
    if not st.session_state.calc_done:
        st.session_state.show_instruction_msg = True

# Case 2: new or changed file uploaded
elif sig != st.session_state.uploaded_signature:
    st.session_state.uploaded_signature = sig
    st.session_state.upload_success = True
    st.session_state.final_df = None
    st.session_state.csv_bytes = None
    st.session_state.calc_done = False
    st.session_state.show_preview = False
    st.session_state.show_instruction_msg = False

# ---------------------- Upload message ----------------------

if st.session_state.upload_success:
    st.success("IAP CSV uploaded successfully!")
# ---------------------- Validators ----------------------

# --------------------- FINANCIAL MONTH (mandatory)
def validate_month_columns(df):
    col = 'FINANCIAL MONTH'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."
    invalid = df[
        df[col].isna() |
        (~pd.to_numeric(df[col], errors="coerce").between(0, 13))]

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

    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    pod = df[pod_col].fillna("").astype("string").str.strip().str.upper()
    cleaned, blank = get_clean_and_blank(df, col)

    invalid_when_pod_non_activity = pod.isin(NON_ACTIVITY_PODS) & (~blank)
    invalid_required_missing = (~pod.isin(BLANK_ALLOWED_PODS)) & blank

    invalid_length = (~blank) & ((cleaned.str.len() < 5) | (cleaned.str.len() > 9))

    invalid = df[invalid_when_pod_non_activity | invalid_required_missing | invalid_length]
    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY) (optional - but treat as mandatory where relevant!)
def validate_gp_practice_columns(df):
    col = 'ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY)'
    pod_col = 'POINT OF DELIVERY CODE'

    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    # Load ICB reference
    # when running locally
#    ref_org = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\reference_tables\ICB_and_SubICB_Apr2026.csv")

#    # when running in stlite
    ref_org_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/ICB_and_SubICB_Apr2026.csv")
    ref_org = pd.read_csv(ref_org_URL)

    # ✅ Decide which codes are valid
    icb_codes = ref_org['ICB_Code'].dropna().astype(str).str.strip()
    org_codes = ref_org['Organisation_Code'].dropna().astype(str).str.strip()
    valid_codes = set(icb_codes).union(set(org_codes))

    pod = df[pod_col].fillna("").astype("string").str.strip().str.upper()
    cleaned, blank = get_clean_and_blank(df, col)

    invalid_when_pod_non_activity = pod.isin(NON_ACTIVITY_PODS) & (~blank)
    invalid_required_missing = (~pod.isin(BLANK_ALLOWED_PODS)) & blank

    invalid_code = (~blank) & (~cleaned.isin(valid_codes))

    invalid = df[invalid_when_pod_non_activity | invalid_required_missing | invalid_code]
    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY) (optional - but treat as mandatory where relevant!)
def validate_residence_resp_columns(df):
    col = 'ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY)'
    pod_col = 'POINT OF DELIVERY CODE'

    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    # Load ICB reference
    # when running locally
#    ref_org = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\reference_tables\ICB_and_SubICB_Apr2026.csv")

#    # when running in stlite
    ref_org_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/ICB_and_SubICB_Apr2026.csv")
    ref_org = pd.read_csv(ref_org_URL)

    icb_codes = ref_org['ICB_Code'].dropna().astype(str).str.strip()
    org_codes = ref_org['Organisation_Code'].dropna().astype(str).str.strip()
    valid_codes = set(icb_codes).union(set(org_codes))

    pod = df[pod_col].fillna("").astype("string").str.strip().str.upper()
    cleaned, blank = get_clean_and_blank(df, col)

    invalid_when_pod_non_activity = pod.isin(NON_ACTIVITY_PODS) & (~blank)
    invalid_required_missing = (~pod.isin(BLANK_ALLOWED_PODS)) & blank

    invalid_code = (~blank) & (~cleaned.isin(valid_codes))

    invalid = df[invalid_when_pod_non_activity | invalid_required_missing | invalid_code]
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
    col = 'ACTIVITY TREATMENT FUNCTION CODE'
    pod_col = 'POINT OF DELIVERY CODE'

    for c in (col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    # when running locally
#    tfc_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\reference_tables\TFC.csv")

    # when running in stlite
    tfc_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/TFC.csv")
    tfc_df = pd.read_csv(tfc_URL)    
    
    valid_codes = set(tfc_df.iloc[:, 0].dropna().astype(str).str.strip())
    pod = df[pod_col].fillna("").astype("string").str.strip().str.upper()
    cleaned, blank = get_clean_and_blank(df, col)

    invalid_when_pod_non_activity = pod.isin(NON_ACTIVITY_PODS) & (~blank)
    invalid_required_missing = (~pod.isin(BLANK_ALLOWED_PODS)) & blank

    invalid_code = (~blank) & (~cleaned.isin(valid_codes))

    invalid = df[invalid_when_pod_non_activity | invalid_required_missing | invalid_code]
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
    
    # when running in stlite
    del_serv_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/Delegationservices_v38.csv")
    del_df = pd.read_csv(del_serv_URL)   

    # ✅ Make reference codes uppercase + trimmed
    valid_codes = {str(v).strip().upper()
        for v in del_df.iloc[:, 0].dropna()}

    # ✅ Clean + normalise user input the same way
    s = df[col].astype("string").str.strip()
    s_up = s.str.upper()

    # ✅ Case-insensitive comparison
    invalid = df[~s_up.isin(valid_codes)]

    return list(invalid.index) if not invalid.empty else "Valid"

# --------------------- SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE (mandatory where relevant)
def validate_specialised_mental_health_code_columns(df):
    col = 'SPECIALISED MENTAL HEALTH SERVICE CATEGORY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    invalid_rows = df[
        df[col].notna() &
        (df[col].astype(str).str.len() > 50)]
    return list(invalid_rows.index) if not invalid_rows.empty else "Valid"



# --------------------- POINT OF DELIVERY CODE (mandatory)
def validate_pod_code_columns(df):
    col = 'POINT OF DELIVERY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    # when running locally
    # npod = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\reference_tables\NPOD.csv")

    # when running in stlite
    NPOD_URL = ("https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/NPOD.csv")
    npod = pd.read_csv(NPOD_URL)

    # Clean and normalise NPOD reference values
    valid_codes = set(
        clean_numeric_text(npod.iloc[:, 0])
        .str.upper()
        .dropna())
    
    pod = (clean_numeric_text(df[col])
        .str.upper())

    # Validity check
    invalid_mask = ~pod.isin(valid_codes)
    
    # Lenght rule validation
    invalid_length = pod.notna() & (pod.str.len() > 10)

    # In valid rows
    invalid = df[invalid_mask | invalid_length]

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
    invalid_mask = get_tariff_invalid_mask(df)

    if invalid_mask is None:
        missing = [c for c in ["TARIFF CODE", "POINT OF DELIVERY CODE"] if c not in df.columns]
        return f"Error: '{missing[0]}' column not found in the data."

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

    for c in (act_col, pod_col):
        if c not in df.columns:
            return f"Error: '{c}' column not found in the data."

    # Normalise POD values
    raw_pod = df[pod_col].astype("string")
    pod = (
        raw_pod
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper())

    pod_unknown = (
        raw_pod.isna() |
        pod.isin({
            "", "N/A", "#N/A", "NA", "#NA",
            "NOT KNOWN", "UNKNOWN", "NOT APPLICABLE", "NONE", "NULL"}))

    # For these PODs, activity must be zero (not blank)
    is_non_activity = pod.isin(NON_ACTIVITY_PODS)
    must_be_zero = is_non_activity | pod_unknown

    # Normalise activity
    raw = df[act_col].astype("string")
    act_str = raw.str.strip()

    has_value = raw.notna() & (act_str != "")
    act_num = pd.to_numeric(act_str.where(has_value), errors="coerce")

    # Format rule (only when a value is provided)
    pattern_ok = act_str.where(has_value).str.fullmatch(r"\d+(\.\d{1,3})?")
    int_part_len = act_str.where(has_value).str.split(".", n=1).str[0].str.len()

    format_invalid = has_value & (
        act_num.isna() |
        (~pattern_ok.fillna(False)) |
        (int_part_len > 10) |
        (act_num < 0))

    # Zero rule:
    # when POD is non‑activity or unknown, activity must be explicitly 0
    zero_rule_invalid = (
        must_be_zero & (
            (~has_value) |
            act_num.isna() |
            (act_num != 0)))

    invalid_rows = df[format_invalid | zero_rule_invalid]
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
    'ORGANISATION IDENTIFIER (GP PRACTICE RESPONSIBILITY)': 'mandatory where relevant',
    'ORGANISATION IDENTIFIER (RESIDENCE RESPONSIBILITY)': 'mandatory where relevant',
    'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)': 'mandatory',
    'GENERAL MEDICAL PRACTICE (PATIENT REGISTRATION)': 'optional',
    'ACTIVITY TREATMENT FUNCTION CODE': 'mandatory where relevant',
    'LOCAL SUB-SPECIALTY CODE': 'optional',
    'WARD CODE': 'mandatory where relevant',
    'COMMISSIONED SERVICE CATEGORY CODE': 'mandatory',
    'SERVICE CODE': 'mandatory',
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
        st.session_state.show_instruction_msg = True
    else:
        try:
            with st.spinner("Running calculations..."):
                df = pd.read_csv(
                    uploaded_lpr,
                    dtype="string",         # read everything safely as string
                    encoding="utf-8-sig")

                df = df.dropna(how="all").copy()

                # Normalise headers so underscores/spaces/case differences don't matter
                df = normalise_dataframe_headers(df)

                # Clean month/year values (before validation)
                if "FINANCIAL MONTH" in df.columns:
                    df["FINANCIAL MONTH"] = clean_numeric_text(df["FINANCIAL MONTH"])

                if "FINANCIAL YEAR" in df.columns:
                    df["FINANCIAL YEAR"] = clean_numeric_text(df["FINANCIAL YEAR"])

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

                raw_results = [
                    validate_month_columns(df),
                    validate_year_columns(df),
                    validate_datetime_columns(df),
                    validate_cop_columns(df),
                    validate_of_treatment_columns(df),
                    validate_gp_practice_columns(df),
                    validate_residence_resp_columns(df),
                    validate_commissioner_code_columns(df),
                    validate_patient_reg_columns(df),
                    validate_activity_TFC_columns(df),
                    validate_local_sub_columns(df),
                    validate_ward_code_columns(df),
                    validate_commissioned_service_code_columns(df),
                    validate_service_code_columns(df),
                    validate_specialised_mental_health_code_columns(df),
                    validate_pod_code_columns(df),
                    validate_pod_further_detail_code_columns(df),
                    validate_pod_further_detail_desc_columns(df),
                    validate_local_pod_code_columns(df),
                    validate_local_pod_desc_columns(df),
                    validate_local_contract_code_columns(df),
                    validate_local_contract_code_desc_columns(df),
                    validate_local_contract_monitoring_code_columns(df),
                    validate_local_contract_monitoring_desc_columns(df),
                    validate_contract_monitoring_detail_columns(df),
                    validate_contract_monitoring_desc_columns(df),
                    validate_tariff_code_columns(df),
                    validate_tariff_indicator_columns(df),
                    validate_contract_monitoring_activity_columns(df),
                    validate_contract_monitoring_price_columns(df),
                    validate_contract_monitoring_market_columns(df),
                    validate_name_of_submitter_columns(df),]

                status = pd.Series(
                    [
                        build_status_with_invalid_values(df, col, result)
                        for col, result in zip(columns, raw_results)],
                    name="Status")


                def build_note(df: pd.DataFrame, col: str) -> str:
                    if col in BLANK_WHEN_NON_ACTIVITY_POD_FIELDS and non_activity_blank_rule_triggered(df, col):
                        return BLANK_RULE_NOTE

                    if col == "CONTRACT MONITORING PLANNED ACTIVITY" and non_activity_zero_rule_triggered(df, col):
                        return CONTR_MON_PLAN_ACT_RULE_NOTE

                    if col == "TARIFF CODE" and tariff_rule_triggered(df):
                        return TARIFF_RULE_NOTE

                    if col in LENGTH_RULES and length_rule_triggered(df, col):
                        return get_length_rule_note(col)

                    return ""

                suggestions = columns.map(lambda c: build_note(df, c)).rename("Suggestions")

                dfs = [columns, requirement, status]

                # Only include suggestions if at least one note is populated
                if suggestions.str.strip().ne("").any():
                    dfs.append(suggestions)

                final_df = pd.concat(dfs, axis=1)

                # Save for preview/download
                csv = final_df.to_csv(index=False)
                st.session_state.csv_bytes = csv.encode("utf-8")
                st.session_state.final_df = final_df
                st.session_state.calc_done = True
                st.session_state.show_preview = False  # do not auto-open
                st.session_state.show_instruction_msg = False

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
                file_name="Analysed IAP DQ checks.csv",
                mime="text/csv",
                key="dq_download_btn",
                use_container_width=True)

    # Inline preview that persists across reruns (only Status column coloured)
    if st.session_state.show_preview:
        with st.container(border=True):
            st.markdown("**This table shows which columns in your IAP Reporting are valid. If data is invalid, the Status column lists the invalid values.**")
            styled = style_results_table(st.session_state.final_df)
            st.dataframe(
                styled,
                use_container_width=True,
                height=560,
                hide_index=True)
            st.button("Close preview", key="close_preview_btn", on_click=lambda: st.session_state.update(show_preview=False))


# ---------------------- Important note ----------------------

st.write("")
st.write("")
st.warning(
    "**Please note that uploading and processing DQ checks through this tool does not "
    "constitute data submission. This tool is solely intended to assess the formatting "
    "of your file.**")
