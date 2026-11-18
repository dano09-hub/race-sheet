#!/usr/bin/env python3
"""Which side of the Irish Sea a racecourse sits on.

One list, shared by the page build and the Kaggle import, so a Dundalk result is
never counted as British in one place and Irish in the other. Northern Irish
tracks (Down Royal, Downpatrick) race under Irish rules and sit with the Irish
courses, which is how the results feeds treat them too.
"""
import re

HOME_COURSES = {
    "Aintree", "Ascot", "Ayr", "Bangor-on-Dee", "Bath", "Beverley", "Brighton",
    "Carlisle", "Cartmel", "Catterick", "Chelmsford", "Chelmsford City",
    "Cheltenham", "Chepstow", "Chester", "Doncaster", "Epsom", "Epsom Downs",
    "Exeter", "Fakenham", "Ffos Las", "Fontwell", "Goodwood", "Hamilton",
    "Haydock", "Hereford", "Hexham", "Huntingdon", "Kelso", "Kempton",
    "Leicester", "Lingfield", "Ludlow", "Market Rasen", "Musselburgh",
    "Newbury", "Newcastle", "Newmarket", "Newton Abbot", "Nottingham", "Perth",
    "Plumpton", "Pontefract", "Redcar", "Ripon", "Royal Ascot", "Salisbury",
    "Sandown", "Sedgefield", "Southwell", "Stratford", "Taunton", "Thirsk",
    "Uttoxeter", "Warwick", "Wetherby", "Wincanton", "Windsor", "Wolverhampton",
    "Worcester", "Yarmouth", "York",
}
IRISH_COURSES = {
    "Ballinrobe", "Bellewstown", "Clonmel", "Cork", "Curragh", "Down Royal",
    "Downpatrick", "Dundalk", "Fairyhouse", "Galway", "Gowran Park", "Kilbeggan",
    "Killarney", "Laytown", "Leopardstown", "Limerick", "Listowel", "Naas",
    "Navan", "Punchestown", "Roscommon", "Sligo", "Thurles", "Tipperary",
    "Tramore", "Wexford",
}


def course_base(value):
    """'Dundalk (AW)' -> 'Dundalk': the course without a surface suffix."""
    return re.sub(r"\s*\([A-Za-z]{2,4}\)", "", str(value or "")).strip()


def region_of(value):
    """'IRE', 'GB', or '' for a course neither list knows."""
    base = course_base(value)
    if base in IRISH_COURSES:
        return "IRE"
    if base in HOME_COURSES:
        return "GB"
    return ""
