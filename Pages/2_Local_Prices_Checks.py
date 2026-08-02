# cd "C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\"

import streamlit as st
import pandas as pd
import io
from datetime import datetime
import os

# ---------------------- Page config (must be first) ----------------------
st.set_page_config(
    page_title="Automated Local Price Reporting DQ checks",
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
if "sheet_name" not in st.session_state:
    st.session_state.sheet_name = None
if "sheet_signature" not in st.session_state:
    st.session_state.sheet_signature = None  # track sheet selection changes

def file_signature(uploaded_file):
    """Create a simple signature of the uploaded file to detect changes."""
    if uploaded_file is None:
        return None
    try:
        return (uploaded_file.name, uploaded_file.size, getattr(uploaded_file, "type", ""))
    except Exception:
        return (uploaded_file.name, getattr(uploaded_file, "size", None), "")

def to_1_based_indices(result, limit=100):
    """
    Standardise validation outputs:
      - If `result` is a list/Index/DataFrame of row indices -> return 1-based, unique, sorted list.
      - If `result` is a string (e.g., 'Valid', 'Empty', errors) -> return as-is.
      - If there are > limit invalid rows -> return 'More than {limit} invalid'.
    """
    if isinstance(result, str):
        return result

    if isinstance(result, pd.DataFrame):
        indices = result.index
    elif isinstance(result, (list, tuple, pd.Index)):
        indices = result
    else:
        return result

    try:
        uniq = sorted(set(int(i) for i in indices))
    except Exception:
        uniq = sorted(set(indices))

    if len(uniq) == 0:
        return "Valid"
    if len(uniq) > limit:
        return f"More than {limit} invalid"

    # 0-based -> 1-based, plus header row offset of 1
    return [i + 2 for i in uniq]


# ---------------------- Header ----------------------
st.image('input_data_other/london_logos_n_name.png', width=1050)
st.title("Automated Local Prices Reporting DQ checks")

# ---------------------- File upload ----------------------
uploaded_lpr = st.file_uploader(
    "📤 **Upload your CSV or Excel (.xlsx) file**",
    type=["csv", "xlsx"],
    help="Upload your Local Prices Reporting here. You can provide the essential tab as a CSV, or select the sheet if you upload an Excel file.")

# If file changes, clear previous results so the UI doesn't show stale data
sig = file_signature(uploaded_lpr)
if sig != st.session_state.uploaded_signature:
    st.session_state.uploaded_signature = sig
    st.session_state.final_df = None
    st.session_state.csv_bytes = None
    st.session_state.calc_done = False
    st.session_state.show_preview = False
    st.session_state.sheet_name = None
    st.session_state.sheet_signature = None

# If Excel uploaded, present sheet selection
sheet_selected = False
if uploaded_lpr is not None and uploaded_lpr.name.lower().endswith(".xlsx"):
    try:
        # Load sheet names without reading the whole sheet
        xls = pd.ExcelFile(uploaded_lpr, engine="openpyxl")
        sheet_names = xls.sheet_names

        default_idx = 0
        # If previous selection exists and is still valid, keep it
        if st.session_state.sheet_name in sheet_names:
            default_idx = sheet_names.index(st.session_state.sheet_name)

        st.session_state.sheet_name = st.selectbox(
            "Select sheet name",
            options=sheet_names,
            index=default_idx if sheet_names else 0,
            help="Choose the sheet that contains the Local Prices Reporting data."
        )

        # Track sheet selection changes
        current_sheet_sig = (uploaded_lpr.name, st.session_state.sheet_name)
        if current_sheet_sig != st.session_state.sheet_signature:
            st.session_state.sheet_signature = current_sheet_sig
            st.session_state.final_df = None
            st.session_state.csv_bytes = None
            st.session_state.calc_done = False
            st.session_state.show_preview = False

        sheet_selected = True
    except Exception as e:
        st.error(f"Could not read sheet names from the Excel file. {e}")

# ---------------------- Validators (logic kept the same) ----------------------
def validate_year_columns(df):
    col = 'FINANCIAL YEAR'
    if col not in df.columns:
        return "Error: 'FINANCIAL YEAR' column not found in the data."

    s = df[col].astype(str)  # keeps behaviour consistent with your current code
    mask = s.str.len().ne(6) & s.str.contains('/', na=False, regex=False)

    idx = df.index[mask]
    return list(idx) if len(idx) else "Valid"



def validate_datetime_columns(df):
    if 'DATE AND TIME DATA SET CREATED' not in df.columns:
        return "Error: 'DATE AND TIME DATA SET CREATED' column not found in the data."

    df['DATE AND TIME DATA SET CREATED'] = df['DATE AND TIME DATA SET CREATED'].astype(str)
    pattern = r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}'
    invalid_datetime_rows = df[~df['DATE AND TIME DATA SET CREATED'].str.match(pattern, na=False)]
    return list(invalid_datetime_rows.index) if not invalid_datetime_rows.empty else "Valid"


def validate_cop_columns(df):
    if 'ORGANISATION IDENTIFIER (CODE OF PROVIDER)' not in df.columns:
        return "Error: 'ORGANISATION IDENTIFIER (CODE OF PROVIDER)' column not found in the data."

    invalid = df[df['ORGANISATION IDENTIFIER (CODE OF PROVIDER)'].astype(str).str.len() < 3]
    invalid = pd.concat([invalid, df[df['ORGANISATION IDENTIFIER (CODE OF PROVIDER)'].astype(str).str.len() > 6]])
    invalid = pd.concat([invalid, df[df['ORGANISATION IDENTIFIER (CODE OF PROVIDER)'].str.endswith('00', na=False)]])
    invalid = pd.concat([invalid, df[df['ORGANISATION IDENTIFIER (CODE OF PROVIDER)'].isnull()]])
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_of_treatment_columns(df):
    if 'ORGANISATION SITE IDENTIFIER (OF TREATMENT)' not in df.columns:
        return "Error: 'ORGANISATION SITE IDENTIFIER (OF TREATMENT)' column not found in the data."

    invalid = df[~df['ORGANISATION SITE IDENTIFIER (OF TREATMENT)'].isna()]
    invalid = invalid[invalid['ORGANISATION SITE IDENTIFIER (OF TREATMENT)'].astype(str).str.len() < 5]
    invalid = pd.concat([invalid, df[df['ORGANISATION SITE IDENTIFIER (OF TREATMENT)'].astype(str).str.len() > 9]])
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_commissioner_code_columns(df):
    if 'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)' not in df.columns:
        return "Error: 'ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)' column not found in the data."

    invalid = df[df['ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)'].isna()]
    invalid = pd.concat([invalid, df[df['ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)'].astype(str).str.len() < 3]])
    invalid = pd.concat([invalid, df[df['ORGANISATION IDENTIFIER (CODE OF COMMISSIONER)'].astype(str).str.len() > 5]])
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_activity_TFC_columns(df):
    required_columns = ['ACTIVITY TREATMENT FUNCTION CODE', 'POINT OF DELIVERY CODE']
    for col in required_columns:
        if col not in df.columns:
            return f"Error: '{col}' column not found in the data."

    tfc_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\TFC.csv")
    valid_codes = set(tfc_df.iloc[:, 0].dropna().astype(str))

    df['ACTIVITY TREATMENT FUNCTION CODE'] = df['ACTIVITY TREATMENT FUNCTION CODE'].astype(str)
    allowed_pod_values = {"ADJUSTMENT", "BLOCK", "CQUIN", "DRUG", "DEVICE", "NAOTHER"}

    invalid_rows = df[
        (~df['ACTIVITY TREATMENT FUNCTION CODE'].isin(valid_codes)) &
        (~df['POINT OF DELIVERY CODE'].isin(allowed_pod_values))
    ]
    return invalid_rows  # list/df handled by to_1_based_indices


def validate_local_sub_columns(df):
    if 'LOCAL SUB-SPECIALTY CODE' not in df.columns:
        return "Error: 'LOCAL SUB-SPECIALTY CODE' column not found in the data."
    invalid = df[~df['LOCAL SUB-SPECIALTY CODE'].isna()]
    invalid = invalid[invalid['LOCAL SUB-SPECIALTY CODE'].astype(str).str.len() > 8]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_comm_serial_n_columns(df):
    if 'COMMISSIONING SERIAL NUMBER' not in df.columns:
        return "Error: 'COMMISSIONING SERIAL NUMBER' column not found in the data."
    invalid = df[~df['COMMISSIONING SERIAL NUMBER'].isna()]
    invalid = invalid[invalid['COMMISSIONING SERIAL NUMBER'].astype(str).str.len() > 6]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_provider_ref_identifier_columns(df):
    if 'PROVIDER REFERENCE IDENTIFIER' not in df.columns:
        return "Error: 'PROVIDER REFERENCE IDENTIFIER' column not found in the data."
    invalid = df[~df['PROVIDER REFERENCE IDENTIFIER'].isna()]
    invalid = invalid[invalid['PROVIDER REFERENCE IDENTIFIER'].astype(str).str.len() > 20]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_nhs_service_cat_n_columns(df):
    if 'NHS SERVICE AGREEMENT LINE NUMBER' not in df.columns:
        return "Error: 'NHS SERVICE AGREEMENT LINE NUMBER' column not found in the data."
    invalid = df[~df['NHS SERVICE AGREEMENT LINE NUMBER'].isna()]
    invalid = invalid[invalid['NHS SERVICE AGREEMENT LINE NUMBER'].astype(str).str.len() > 10]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_commissioned_service_code_columns(df):
    if 'COMMISSIONED SERVICE CATEGORY CODE' not in df.columns:
        return "Error: 'COMMISSIONED SERVICE CATEGORY CODE' column not found in the data."

    invalid = df[df['COMMISSIONED SERVICE CATEGORY CODE'].isna()]
    invalid = pd.concat([invalid, df[df['COMMISSIONED SERVICE CATEGORY CODE'].astype(str).str.len() != 2]])

    npod_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\Service Codes.csv")
    valid_codes = set(npod_df.iloc[:, 0].dropna().astype(str))
    df['COMMISSIONED SERVICE CATEGORY CODE'] = df['COMMISSIONED SERVICE CATEGORY CODE'].astype(str)
    invalid = df[~df['COMMISSIONED SERVICE CATEGORY CODE'].isin(valid_codes)]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_service_code_columns(df):
    if 'SERVICE CODE' not in df.columns:
        return "Error: 'SERVICE CODE' column not found in the data."

    # (Original behaviour – validates TARIFF CODE length inside this function)
    invalid = df[~df['TARIFF CODE'].isna()]
    invalid = invalid[invalid['TARIFF CODE'].astype(str).str.len() > 12]

    npod_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks\reference_tables\Delegationservices_v37.csv")
    valid_codes = set(npod_df.iloc[:, 0].dropna().astype(str))
    df['SERVICE CODE'] = df['SERVICE CODE'].astype(str)

    invalid = df[~df['SERVICE CODE'].isin(valid_codes)]
    return list(invalid.index) if not invalid.empty else "Valid"


# --------------------- POINT OF DELIVERY CODE (mandatory)
def validate_pod_code_columns(df):
    col = 'POINT OF DELIVERY CODE'
    if col not in df.columns:
        return f"Error: '{col}' column not found in the data."

    # when running locally
    # npod = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating_IAPs_&_Local_Prices_DQ_checks\reference_tables\NPOD.csv")

    # when running in stlite
    NPOD_URL = "https://raw.githubusercontent.com/pete4nhs/DQ_checks/main/reference_tables/NPOD.csv"
    npod = pd.read_csv(NPOD_URL)

    # Clean and normalise reference values
    valid_codes = set(
        clean_numeric_text(npod.iloc[:, 0])
        .str.upper()
        .dropna())

    # Clean and normalise uploaded values
    pod = (
        clean_numeric_text(df[col])
        .str.upper())

    # Length rule
    invalid_length = pod.notna() & (pod.str.len() > 10)

    # Value rule
    invalid_code = ~pod.isin(valid_codes)
    invalid = df[invalid_length | invalid_code]

    return list(invalid.index) if not invalid.empty else "Valid"


def validate_pod_further_detail_code_columns(df):
    if 'POINT OF DELIVERY FURTHER DETAIL CODE' not in df.columns:
        return "Error: 'POINT OF DELIVERY FURTHER DETAIL CODE' column not found in the data."
    invalid = df[~df['POINT OF DELIVERY FURTHER DETAIL CODE'].isna()]
    invalid = invalid[invalid['POINT OF DELIVERY FURTHER DETAIL CODE'].astype(str).str.len() > 10]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_pod_further_detail_desc_columns(df):
    if 'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION' not in df.columns:
        return "Error: 'POINT OF DELIVERY FURTHER DETAIL DESCRIPTION' column not found in the data."
    invalid = df[~df['POINT OF DELIVERY FURTHER DETAIL DESCRIPTION'].isna()]
    invalid = invalid[invalid['POINT OF DELIVERY FURTHER DETAIL DESCRIPTION'].astype(str).str.len() > 100]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_local_pod_code_columns(df):
    if 'LOCAL POINT OF DELIVERY CODE' not in df.columns:
        return "Error: 'LOCAL POINT OF DELIVERY CODE' column not found in the data."
    invalid = df[~df['LOCAL POINT OF DELIVERY CODE'].isna()]
    invalid = invalid[invalid['LOCAL POINT OF DELIVERY CODE'].astype(str).str.len() > 50]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_local_pod_desc_columns(df):
    if 'LOCAL POINT OF DELIVERY DESCRIPTION' not in df.columns:
        return "Error: 'LOCAL POINT OF DELIVERY DESCRIPTION' column not found in the data."
    invalid = df[~df['LOCAL POINT OF DELIVERY DESCRIPTION'].isna()]
    invalid = invalid[invalid['LOCAL POINT OF DELIVERY DESCRIPTION'].astype(str).str.len() > 100]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_tariff_code_columns(df):
    if 'TARIFF CODE' not in df.columns:
        return "Error: 'TARIFF CODE' column not found in the data."
    invalid = df[~df['TARIFF CODE'].isna()]
    invalid = invalid[invalid['TARIFF CODE'].astype(str).str.len() > 10]

    npod_df = pd.read_csv(r"C:\Users\peter.saiu\OneDrive - NHS\Scripts\Python\Automating Local Prices checks/reference_tables/HRG.csv")
    valid_codes = set(npod_df.iloc[:, 0].dropna().astype(str))
    df['TARIFF CODE'] = df['TARIFF CODE'].astype(str)
    invalid = df[~df['TARIFF CODE'].isin(valid_codes)]
    return list(invalid.index) if not invalid.empty else "Valid"


def validate_local_price_columns(df):
    col = 'LOCAL PRICE'
    if col not in df.columns:
        return "Error: 'LOCAL PRICE' column not found in the data."

    series = df[col]
    str_vals = series.astype(str)
    is_empty_value = series.isna() | (str_vals.str.strip() == "")

    if is_empty_value.all():
        return "Empty"

    invalid_mask = is_empty_value.copy()
    non_empty = ~is_empty_value
    vals = str_vals[non_empty].str.strip()

    decimal_pattern_mask = vals.str.fullmatch(r"\d+(\.\d{1,2})?")
    digit_count_ok_mask = vals.str.replace(".", "", regex=False).str.len() <= 18
    non_empty_invalid = ~(decimal_pattern_mask & digit_count_ok_mask)
    invalid_mask.loc[non_empty] = non_empty_invalid

    invalid_indices = list(series.index[invalid_mask])
    if len(invalid_indices) == 0:
        return "Valid"
    if len(invalid_indices) > 100:
        return "More than 100 invalid"
    return invalid_indices


# ---------------------- Run checks button ----------------------
if st.button("Run checks", type="primary"):
    if uploaded_lpr is None:
        st.warning("Please upload a CSV or Excel file before calculating.")
    else:
        try:
            with st.spinner("Running calculations..."):
                # Read data depending on file type
                if uploaded_lpr.name.lower().endswith(".csv"):
                    df = pd.read_csv(uploaded_lpr)
                    st.success("Local Prices Reporting CSV uploaded successfully!")
                else:
                    if not sheet_selected or not st.session_state.sheet_name:
                        st.warning("Please select a sheet before running checks.")
                        st.stop()
                    # Rewind the buffer since ExcelFile may have read from it
                    uploaded_lpr.seek(0)
                    df = pd.read_excel(uploaded_lpr, sheet_name=st.session_state.sheet_name, engine="openpyxl")
                    st.success(f"Local Prices Reporting Excel uploaded successfully (sheet: {st.session_state.sheet_name})!")

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
                ], name='Column_name')

                value = pd.Series([
                    to_1_based_indices(validate_year_columns(df)),
                    to_1_based_indices(validate_datetime_columns(df)),
                    to_1_based_indices(validate_cop_columns(df)),
                    to_1_based_indices(validate_of_treatment_columns(df)),
                    to_1_based_indices(validate_commissioner_code_columns(df)),
                    to_1_based_indices(validate_activity_TFC_columns(df)),
                    to_1_based_indices(validate_local_sub_columns(df)),
                    to_1_based_indices(validate_comm_serial_n_columns(df)),
                    to_1_based_indices(validate_provider_ref_identifier_columns(df)),
                    to_1_based_indices(validate_nhs_service_cat_n_columns(df)),
                    to_1_based_indices(validate_commissioned_service_code_columns(df)),
                    to_1_based_indices(validate_service_code_columns(df)),
                    to_1_based_indices(validate_pod_code_columns(df)),
                    to_1_based_indices(validate_pod_further_detail_code_columns(df)),
                    to_1_based_indices(validate_pod_further_detail_desc_columns(df)),
                    to_1_based_indices(validate_local_pod_code_columns(df)),
                    to_1_based_indices(validate_local_pod_desc_columns(df)),
                    to_1_based_indices(validate_tariff_code_columns(df)),
                    to_1_based_indices(validate_local_price_columns(df)),
                ], name="Status")

                final_df = pd.concat([columns, value], axis=1)

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
                file_name="Analysed Local Prices DQ checks.csv",
                mime="text/csv",
                key="dq_download_btn",
                use_container_width=True
            )

    # Inline preview that persists across reruns
    if st.session_state.show_preview:
        with st.container(border=True):
            st.markdown("**This table shows which columns in your Local Prices Reporting are valid. If data is invalid, the Status column lists the row numbers with incorrect formatting.**")
            st.dataframe(
                st.session_state.final_df,
                use_container_width=True,
                height=560, hide_index=True)
            
            st.button("Close preview", key="close_preview_btn", on_click=lambda: st.session_state.update(show_preview=False))

else:
    st.info("Please upload a CSV or Excel file and click Run checks.")
