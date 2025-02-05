"""contains shared functions for cleaning data"""

import argparse
import logging
import re
import numpy as np
import pandas as pd
from tqdm import tqdm
import constants

logging.basicConfig(
    filename="clean_crdc_data.log", filemode="w", level=logging.INFO
)


def read_file(file_path: str) -> pd.DataFrame:
    """Reads a file and returns a dataframe"""
    if file_path.endswith(".csv") or file_path.endswith(".csv.gz"):
        return pd.read_csv(file_path, encoding="latin-1", low_memory=False)
    if file_path.endswith(".xlsx"):
        return pd.read_excel(file_path)
    raise ValueError("File type not supported")


def cols_to_lower(df: pd.DataFrame) -> pd.DataFrame:
    """converts all column names to lowercase"""
    df.columns = df.columns.str.lower()
    return df


def drop_reserve_codes(df: pd.DataFrame) -> pd.DataFrame:
    """drops reserve codes from the dataframe"""
    return (
        df.set_index(constants.TEXT_COLS).pipe(lambda d: d.mask(d.lt(0))).reset_index()
    )


def get_school_year(school_year: str) -> int:
    """gets the year from the school year"""
    school_match = re.search(r"\d{4}-\d{2}", school_year)
    if school_match:
        return int(school_match.group()[:4])

    school_match = re.search(r"\d{4}", school_year)
    if school_match:
        return int("20" + school_match.group()[:2])

    raise ValueError(f"couldn't extract school year from {school_year}")


def get_max_grade(df: pd.DataFrame) -> pd.DataFrame:
    """gets the max grade from the grade range"""

    def get_grade_for_row(row):
        true_cols = [
            (k, v) for k, v in row.items() if k in constants.GRADE_COLS and v == "Yes"
        ]
        if len(true_cols) == 0:
            return np.NaN
        return int(re.search(r"(?<=SCH_GRADE_G)\d{1,2}$", true_cols[-1][0]).group())

    return df.assign(max_grade=df.apply(get_grade_for_row, axis=1)).drop(
        constants.GRADE_COLS, axis=1
    )


def drop_duplicates_keep_most_complete(df: pd.DataFrame) -> pd.DataFrame:
    """drops duplicates and keeps the most complete row"""
    orig_len = len(df)
    result = (
        df.assign(
            completeness_score=lambda df: df.apply(
                lambda row: sum(1 for key in row.index if pd.notna(row[key])), axis=1
            )
        )
        .sort_values("completeness_score", ascending=False)
        .drop_duplicates(subset=["LEAID", "SCHID"], keep="first")
        .drop("completeness_score", axis=1)
    )
    logging.info(
        "dropped %s duplicate rows (%s%% of %s total)",
        orig_len - len(result),
        round((orig_len - len(result)) / orig_len * 100, 2),
        orig_len,
    )
    return result


def select_cols(df: pd.DataFrame) -> pd.DataFrame:
    """selects columns from the dataframe"""
    return df.set_index([c for c in df.columns if c in constants.TEXT_COLS])[
        [c for c in df.columns if c in constants.NUMERIC_COLS]
    ].reset_index()


def preprocess_df(df: pd.DataFrame, year) -> pd.DataFrame:
    """preprocesses the dataframe"""
    return (
        df.pipe(select_cols)
        .pipe(drop_reserve_codes)
        .pipe(get_max_grade)
        .pipe(drop_duplicates_keep_most_complete)
        .assign(year=year)
        # assign full school ID
        .assign(
            full_school_id=lambda df: df.LEAID.astype(str).str.zfill(7) + df.SCHID.astype(str).str.zfill(5)
        )
    )


def read_segmented_dfs(*filenames) -> pd.DataFrame:
    """reads and stacks the dataframes"""
    df = (
        pd.DataFrame()
        .join(
            [
                read_file(f).pipe(select_cols).set_index(constants.INDEX_COLS)
                for f in filenames
            ],
            how="outer",
        )
        .reset_index()
    )
    return df


def read_stack_dfs(*filenames) -> pd.DataFrame:
    """reads and stacks the dataframes"""
    dfs = []
    counter = tqdm()
    for filename in filenames:
        logging.info("Cleaning %s", filename)
        counter.update(1)
        counter.set_description(f"Cleaning {filename}")

        match filename:
            case str():
                dfs.append(
                    preprocess_df(
                        read_file(filename),
                        get_school_year(filename),
                    )
                )

            case tuple() | list():
                dfs.append(
                    preprocess_df(
                        read_segmented_dfs(*filename), get_school_year(filename[0])
                    )
                )

            case _:
                raise ValueError(f"unknown filename type {type(filename)}")

    return pd.concat(dfs)


def calculate_totals(df: pd.DataFrame) -> pd.DataFrame:
    """calculates total columns"""
    # assign overall totals
    df["total_arrests"] = (
        df.TOT_DISCWDIS_ARR_IDEA_F
        + df.TOT_DISCWDIS_ARR_IDEA_M
        + df.TOT_DISCWODIS_ARR_F
        + df.TOT_DISCWODIS_ARR_M
    )
    df["total_referrals"] = (
        df.TOT_DISCWDIS_REF_IDEA_F
        + df.TOT_DISCWDIS_REF_IDEA_M
        + df.TOT_DISCWODIS_REF_F
        + df.TOT_DISCWODIS_REF_M
    )
    df["total_referrals_arrests"] = df.total_arrests + df.total_referrals
    df["total_enrollment"] = df.TOT_ENR_F + df.TOT_ENR_M

    # assign totals by race
    for race_abbr in constants.RACE_VALUES:
        abbr_upper = race_abbr.upper()
        df[f"total_arrests_{race_abbr}"] = (
            df[f"SCH_DISCWDIS_ARR_IDEA_{abbr_upper}_F"]
            + df[f"SCH_DISCWDIS_ARR_IDEA_{abbr_upper}_M"]
            + df[f"SCH_DISCWODIS_ARR_{abbr_upper}_F"]
            + df[f"SCH_DISCWODIS_ARR_{abbr_upper}_M"]
        )
        df[f"total_referrals_{race_abbr}"] = (
            df[f"SCH_DISCWDIS_REF_IDEA_{abbr_upper}_F"]
            + df[f"SCH_DISCWDIS_REF_IDEA_{abbr_upper}_M"]
            + df[f"SCH_DISCWODIS_REF_{abbr_upper}_F"]
            + df[f"SCH_DISCWODIS_REF_{abbr_upper}_M"]
        )
        df[f"total_enrollment_{race_abbr}"] = (
            df[f"SCH_ENR_{abbr_upper}_F"] + df[f"SCH_ENR_{abbr_upper}_M"]
        )

    # assign totals for disability status
    df["total_arrests_idea"] = df.TOT_DISCWDIS_ARR_IDEA_F + df.TOT_DISCWDIS_ARR_IDEA_M
    df["total_arrests_nondis"] = df.TOT_DISCWODIS_ARR_F + df.TOT_DISCWODIS_ARR_M
    df["total_referrals_idea"] = df.TOT_DISCWDIS_REF_IDEA_F + df.TOT_DISCWDIS_REF_IDEA_M
    df["total_referrals_nondis"] = df.TOT_DISCWODIS_REF_F + df.TOT_DISCWODIS_REF_M
    df["total_enrollment_idea"] = df.TOT_IDEAENR_F + df.TOT_IDEAENR_M
    df["total_enrollment_nondis"] = df.TOT_ENR_F + df.TOT_ENR_M

    return df


def drop_rows_with_data_entry_errors(df: pd.DataFrame) -> pd.DataFrame:
    """
    drops rows with inaccurate data due to data entry errors

    see bulletproof/drop_rows_with_data_entry_errors.py for more details
    """
    # drop rows with arrest or enrollment rates over 100%
    start_len = len(df)
    df = df.query(
        "~(total_arrests > total_enrollment | total_referrals > total_enrollment)"
    )
    logging.info(
        "dropped %s rows with arrest or referral rates over 100%% (%s%% of %s total)",
        start_len - len(df),
        round((start_len - len(df)) / start_len * 100, 2),
        len(df),
    )

    # drop rows with more arrests than referrals
    start_len = len(df)
    df = df.query("~(total_arrests > total_referrals)")
    new_len = len(df)
    logging.info(
        "dropped %s rows with more arrests than referrals (%s%% of %s total)",
        start_len - len(df),
        round((start_len - len(df)) / start_len * 100, 2),
        new_len
    )

    # drop schools with very high totals and near-identical arrest and referral rates
    # assign temporary columns to make the query easier to read
    df = df.assign(
        grade_category=lambda df: df.apply(
            lambda row: "high school"
            if row.max_grade in range(10, 13)
            else "middle school"
            if row.max_grade in range(7, 10)
            else "elementary school"
            if row.max_grade in range(1, 7)
            else "other",
            axis=1,
        )
    )
    # create a dataframe of thresholds for each grade category and year
    threshold_df = (
        df.groupby(["grade_category", "year"])
        .total_referrals_arrests.quantile(0.999)
        .to_frame("threshold")
    )
    # merge the thresholds into the main dataframe and drop rows that meet criteria
    start_len = len(df)
    close_vals = list(range(0, 3))  # pylint: disable=unused-variable
    df = df.merge(
        threshold_df, left_on=["grade_category", "year"], right_index=True
    ).query(
        "~(total_referrals_arrests > threshold "
        "& abs(total_arrests - total_referrals) in @close_vals)"
    )
    new_len = len(df)
    logging.info(
        "dropped %s rows with very high totals and near-identical arrest and referral rates (%s%% of %s total)",
        start_len - new_len,
        round((start_len - new_len) / start_len * 100, 2),
        new_len
    )
    return df


def drop_unwanted_schools(df: pd.DataFrame) -> pd.DataFrame:
    """drops schools that are too small, or are alternative or juvenile justice schools"""

    def does_not_contain_keyword(sch_name):
        """checks if school name contains keywords"""
        for keyword in constants.DROP_SCHOOLS_KWDS:
            if re.search(keyword, sch_name, re.IGNORECASE):
                return False
        return True

    start_len = len(df)
    df = (
        df.query("total_enrollment >= 50")
        .query("JJ == 'No' | JJ == False")
        .query("SCH_STATUS_ALT == 'No' | SCH_STATUS_ALT == False")
        .pipe(lambda df: df[df.SCH_NAME.apply(does_not_contain_keyword)])
    )
    new_len = len(df)
    logging.info(
        "dropped %s schools that were alternative or too small (%s%% of %s total)", 
        start_len - new_len,
        round((start_len - new_len) / start_len * 100, 2),
        new_len,
    )
    return df


def drop_manually_excluded_schools(df: pd.DataFrame) -> pd.DataFrame:
    """drops schools that were manually excluded for a given year"""
    # MANUAL_EXCLUSION_SCHOOLS is a dict of year and NCES IDs for schools to ignore for certain years
    start_len = len(df)
    for year, nces_ids in constants.MANUAL_EXCLUSION_SCHOOLS.items():
        df = df.query("~(year == @year & full_school_id in @nces_ids)")
    logging.info(
        "dropped %s schools that were manually excluded (%s%% of %s total)",
        start_len - len(df),
        round((start_len - len(df)) / start_len * 100, 2),
        len(df),
    )
    return df


def main(*input_files, output_file):
    """main function"""
    (
        read_stack_dfs(*input_files)
        .pipe(calculate_totals)
        .pipe(drop_rows_with_data_entry_errors)
        .pipe(drop_unwanted_schools)
        .pipe(drop_manually_excluded_schools)
        .to_csv(output_file, index=False)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year-files", "-f", nargs="+", action="append", required=True)
    parser.add_argument("--output-file", "-o", required=True)
    args = parser.parse_args()

    main(*args.year_files, output_file=args.output_file)
