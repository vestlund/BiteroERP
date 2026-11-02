"""Datumformat i hela programmet: ÅÅÅÅ-MM-DD (ISO 8601), som i svensk standard.

Ersätter bara de namn som anges här; övrigt (decimalkomma, tusentalsavgränsare) följer Djangos svenska format.
"""

DATE_FORMAT = "Y-m-d"
SHORT_DATE_FORMAT = "Y-m-d"
DATETIME_FORMAT = "Y-m-d H:i"
SHORT_DATETIME_FORMAT = "Y-m-d H:i"
MONTH_DAY_FORMAT = "m-d"
YEAR_MONTH_FORMAT = "Y-m"
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%Y%m%d"]
DATETIME_INPUT_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]
FIRST_DAY_OF_WEEK = 1
