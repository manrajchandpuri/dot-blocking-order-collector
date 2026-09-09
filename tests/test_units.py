"""Unit coverage for the parsing rules that the corpus taught us."""
import datetime as dt

import pytest

from dotbo.letter import parse_letter
from dotbo.naming import order_stem, sequence, Order
from dotbo.catalog import Attachment
from dotbo.letter import Letter
from dotbo.urls import candidates_from_lines, drop_fragments, is_noise, normalise_key


def texts(lines):
    return [c.text for c in candidates_from_lines(lines)]


# ---------------------------------------------------------------- counts

@pytest.mark.parametrize(
    "body,expected",
    [
        ("blocking access to the websites [ 12 nos] enumerated in Document A", 12),
        ("blocking access to the domain names [ 85 nos.] , enumerated in", 85),
        ("unblocking access to website(s)/domain(s) [ 01 No. ], as per the enclosed list", 1),
        ("blocking of websites [2+9=11 nos] enumerated in the 39th and 40th Addl. Lists", 11),
        ("blocking of websites [8+10=18 nos] enumerated in the 41st and 42nd Addl. Lists", 18),
        ("blocking access to the said websites, as above, in compliance", None),
        # The 2026 DGT letters state the count in several other shapes.
        ("blocking access to website(s)/domain(s) [402 Nos] (Defendant Nos. 25 to 426)", 402),
        ("blocking access to the website(s)/domain(s) [102 Nos no.], impleaded as", 102),
        ("blocking access to website(s)/domain(s) [05 Nos (Defendant No.1)], as per", 5),
        (
            "blocking access to website(s)/domain(s) [198 Nos. (132 (Defendant No. 01 to 30)"
            " + 66 (Defendant No.63 to 128)], as per the enclosed list",
            198,
        ),
        ("blocking access to the 586 domains/URLs as listed in the enclosed list", 586),
        ("blocking access to the 5 no. of website as enumerated in para 5 of", 5),
        # A URL inside the bracket is not a count.
        ("blocking access to the impugned website/domain [www.example.com], enumerated", None),
    ],
)
def test_declared_count(body, expected):
    letter = parse_letter("Dated: 01-05-2025 To, Subject: X. Please refer to " + body + " Encl: A/A")
    assert letter.declared_count == expected


def test_order_date_from_letter():
    assert parse_letter("No. 813-7/25/2024-DS-II   Dated: 01-05-2025").order_date == dt.date(2025, 5, 1)
    assert parse_letter("Date: \n07-08-2026").order_date == dt.date(2026, 8, 7)


def test_unblocking_is_detected():
    letter = parse_letter(
        "Dated: 07-08-2026 Subject: CS(COMM) 420/2023: X. Please refer to para(s) 14 "
        "of the said court order regarding unblocking access to website(s)/domain(s) "
        "[ 01 No. ], as per the enclosed list. Encl: A/A"
    )
    assert letter.action == "unblock"


def test_inline_url_in_the_directive():
    """May 37_: the only blocked URL is named in the letter, not in an annexure."""
    letter = parse_letter(
        "Dated: 20-05-2025 Subject: CS (COMM) 6 of 2025: GLOBAL HEALTH LIMITED versus "
        "JOHN DOE in the High Court of Delhi. Please find enclosed the order dated "
        "08.01.2025 in the captioned suit. 2. In compliance with the said Court order, "
        "all Licensees are instructed to take action in respect of blocking access to "
        "the website https://backpotin.com , mentioned in the para 39 of the said Court "
        "order. Encl: A/A"
    )
    assert [c.text for c in letter.inline_urls] == ["https://backpotin.com"]


def test_subject_domain_is_not_treated_as_a_target():
    """May 10_'s subject names the lead defendant HTTPS//CRICHDPLAYER.ORG/."""
    letter = parse_letter(
        "Dated: 05-05-2025 Subject: CS(COMM) 266 of 2025: STAR INDIA PRIVATE LIMITED "
        "versus HTTPS//CRICHDPLAYER.ORG/ & ORS. in the High Court of Delhi. Please find "
        "enclosed the order dated 25.03.2025. 2. Please refer to para 33(i & j) in "
        "respect of blocking of websites [2+9=11 nos] enumerated in the 39th and 40th "
        "Addl. Lists of websites. Encl: A/A"
    )
    assert letter.inline_urls == []


@pytest.mark.parametrize(
    "subject,case,court",
    [
        ("CS(COMM) 420/2023: Asian Paints Ltd v. Ajeet Kumar & Ors. in the Hon'ble "
         "High Court of Delhi.", "CS(COMM) 420/2023", "High Court of Delhi"),
        ("CS (COMM) 87 of 2023: STAR INDIA versus MOVIESVERSE.AC & ORS. in the High "
         "Court of Delhi.", "CS (COMM) 87 of 2023", "High Court of Delhi"),
        ("COMIPS (L) No. 18000 of 2026: X v. Y in the Hon'ble Bombay High Court.",
         "COMIPS (L) No. 18000 of 2026", "Bombay High Court"),
        ("Writ-A No. 30480 of 2026: Sahil Singh vs. MeitY & Ors. in the Hon'ble High "
         "Court of Judicature at Allahabad.", "Writ-A No. 30480 of 2026",
         "High Court of Judicature at Allahabad"),
    ],
)
def test_case_and_court_are_parsed(subject, case, court):
    letter = parse_letter(
        "Dated: 02-09-2026 To, All Licensees with Internet Service Authorization "
        "Subject: " + subject + " Please refer to para 1. Encl: A/A"
    )
    assert letter.case_no == case
    assert letter.court == court


# ---------------------------------------------------------------- URLs

def test_dewraps_across_line_breaks():
    """May 21_: the annexure wraps 'hbytrcnlkssx.xyz' mid-token."""
    assert texts(["openbestai.com,", "hbytrcnlkss", "x.xyz, fdcovhsv.com"]) == [
        "openbestai.com", "hbytrcnlkssx.xyz", "fdcovhsv.com",
    ]


def test_dewraps_within_a_line():
    assert texts(["zudiofashionretailfranchis e.com"]) == ["zudiofashionretailfranchise.com"]
    assert texts(["tatatrentzudiofranchise.co m"]) == ["tatatrentzudiofranchise.com"]


def test_does_not_glue_two_consecutive_list_entries():
    """May 38_: '1moviesonline' and '3kmovies.beauty' are separate entries."""
    assert texts(["1moviesonline", "3kmovies.beauty"]) == ["1moviesonline", "3kmovies.beauty"][1:]


def test_registrar_column_is_excluded():
    """May 21_: 'GoDaddy.com, LLC' sits beside the rogue domains, not among them."""
    assert texts([
        "GoDaddy.com, LLC  (D. No. 6)",
        "1357c.cc, F666666.xyz, 6868a.cc (D. No. 1)",
    ]) == ["1357c.cc", "F666666.xyz", "6868a.cc"]


def test_emails_are_never_urls():
    assert texts(["Email: dirds2-dot@nic.in", "(chinnasamy.v@meity.gov.in)"]) == []


def test_wrapped_email_domain_is_not_a_url():
    """May 12_: '...abbe14cc1b@p' / 'rivacyguardian.org' is one email address."""
    assert texts([
        "https://dilhoaaapka.store/",
        "pwp-",
        "0cb88286a3348cc1de6133abbe14cc1b@p",
        "rivacyguardian.org",
    ]) == ["https://dilhoaaapka.store/"]


def test_prose_across_a_full_stop_is_not_a_url():
    assert texts(["infringing the plaintiff's copyright. In such a situation"]) == []
    assert texts(["...at the given address. It is submitted"]) == []


def test_multi_label_hosts_survive():
    kept = ["https://m.moviediskhd.shop", "https://a52.azplay7.me", "https://www.037hdmovie.com"]
    assert texts(kept) == kept


def test_public_suffixes_are_rejected():
    assert is_noise("co.in") and is_noise("com.au")
    assert not is_noise("zudio.co")


def test_fragment_of_a_wrapped_domain_is_dropped():
    """May 20_: the table interleaves an IP column between the two halves."""
    cands = candidates_from_lines(["https://popcornmovies.to", "104.21.45.207", "es.to"])
    assert [c.key for c in drop_fragments(cands)] == ["popcornmovies.to"]


def test_dewraps_a_url_that_wrapped_after_its_scheme():
    """Order 82667: the guard used to reject any fragment containing "://"."""
    assert texts([
        "172.67.197.134", "https://live1.streambylivepulse.c", "om", "GoDaddy.com, LLC",
    ]) == ["https://live1.streambylivepulse.com"]


def test_dewraps_a_tld_that_wrapped_after_the_dot():
    assert texts(["https://watch.buffstreamsbackup.", "world", "NameCheap, Inc."]) == [
        "https://watch.buffstreamsbackup.world"
    ]


def test_clips_a_url_run_into_the_next_column():
    """No separator between the URL and the registrar name beside it."""
    assert texts(["https://ffff.onlineworldcupfifa.comCloudflare, Inc."]) == [
        "https://ffff.onlineworldcupfifa.com"
    ]


def test_ip_with_a_scheme_is_a_target_but_a_bare_one_is_not():
    """Order 82667 lists nine IPs as targets; May 20_ has an IP *column*."""
    assert texts(["http://103.165.93.31", "http://107.150.39.250"]) == [
        "http://103.165.93.31", "http://107.150.39.250",
    ]
    assert texts(["104.21.60.156", "172.67.218.221"]) == []


def test_a_hash_subdomain_survives_but_a_hash_domain_does_not():
    assert texts(["https://a0504dae9c72cdb02b00", "ddedf6df4973.livehwc4.com"]) == [
        "https://a0504dae9c72cdb02b00ddedf6df4973.livehwc4.com"
    ]
    assert texts(["c22cf6a39a0943ffacfe8ad5c034327e.pr", "otect@withheldforprivacy.com"]) == []


def test_occurrences_can_be_counted_without_de_duplicating():
    """DoT declares rows, not distinct URLs: 82667 says 586 rows, 575 distinct."""
    from dotbo.urls import candidates_from_lines as raw
    lines = ["a.example.com", "b.example.com", "a.example.com"]
    assert len(raw(lines)) == 2
    assert len(raw(lines, dedupe=False)) == 3


def test_bare_ip_addresses_are_not_listed():
    """The annexure's IP column is not what anyone was ordered to block."""
    assert texts(["https://gojo.wtf", "172.67.218.221", "104.21.27.84"]) == ["https://gojo.wtf"]


def test_normalise_key():
    assert normalise_key("https://www.Example.COM/") == "example.com"
    assert normalise_key("Example.com") == "example.com"


# ---------------------------------------------------------------- naming

def test_order_stem_uses_the_may_convention():
    assert order_stem(1, dt.date(2026, 8, 5)) == "1_Blocking order dated 05 August 2026"
    assert order_stem(13, dt.date(2026, 8, 18)) == "13_Blocking order dated 18 August 2026"


def test_sequence_sorts_by_date_then_catalogue_position():
    def make(att_id, seq, day):
        return Order(
            attachment=Attachment(att_id=att_id, listing_id=1, seq=seq),
            letter=Letter(order_date=dt.date(2026, 8, day)),
        )

    ordered = sequence([make(3, 2, 7), make(1, 0, 5), make(2, 1, 5)])
    assert [o.attachment.att_id for o in ordered] == [1, 2, 3]
    assert [o.index for o in ordered] == [1, 2, 3]
