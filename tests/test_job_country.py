"""Which country a listing belongs to, and what the intake gate now allows."""
import pytest

from app.services.job_country import (
    DEFAULT,
    NAMES,
    country_code,
    country_name,
    known_codes,
)


class TestNamingACountryOutright:
    @pytest.mark.parametrize(
        "location,code",
        [
            ("United States", "US"),
            ("USA", "US"),
            ("Remote, US", "US"),
            ("United States (Remote)", "US"),
            ("U.S. - Remote", "US"),
            ("Europe,  UK", "GB"),
            ("London, United Kingdom", "GB"),
            ("Brazil", "BR"),
            ("Recife, Brasil", "BR"),
            ("EMEA,  Germany", "DE"),
            ("Toronto, Canada", "CA"),
            ("Bangalore, India", "IN"),
            ("Sydney, Australia", "AU"),
            ("Tel Aviv, Israel", "IL"),
            ("Remote - Poland", "PL"),
        ],
    )
    def test_the_named_country_wins(self, location, code):
        assert country_code(location) == code


class TestWhenNoCountryIsNamed:
    @pytest.mark.parametrize(
        "location",
        ["", "   ", None, "Remote", "Anywhere", "Blue Bell", "New York City",
         "San Francisco, CA", "Boston, MA", "Santa Clara", "Kingston,"],
    )
    def test_it_is_treated_as_the_united_states(self, location):
        # The operator's rule: a listing without a country is a US listing.
        assert country_code(location) == "US"

    def test_a_bare_foreign_city_also_falls_back(self):
        # Documented consequence, not an oversight: "Warsaw" names no country,
        # and guessing from city names alone misfires (London is in Ontario and
        # Kentucky too).
        assert country_code("Warsaw") == "US"
        assert country_code("Bangkok") == "US"


class TestTheTrapsThatWouldMoveRowsWrongly:
    def test_new_mexico_is_not_mexico(self):
        assert country_code("Albuquerque, New Mexico") == "US"
        assert country_code("New Mexico") == "US"

    def test_mexico_itself_still_reads_as_mexico(self):
        assert country_code("Mexico City, Mexico") == "MX"

    def test_georgia_stays_us_because_the_state_is_likelier_here(self):
        assert country_code("Atlanta, Georgia") == "US"

    def test_the_pronoun_us_cannot_drag_a_row_anywhere_new(self):
        # A false match toward US is free — US is the fallback regardless.
        assert country_code("Come work with us") == "US"

    def test_a_country_inside_a_longer_word_is_not_matched(self):
        assert country_code("Chinatown, San Francisco") == "US"


class TestSeveralCountriesAtOnce:
    def test_the_united_states_wins_when_it_is_among_them(self):
        assert country_code("LATAM,  Canada,  USA") == "US"
        assert country_code("UK, United States") == "US"

    def test_otherwise_the_earliest_named_country_wins(self):
        assert country_code("Denmark,  Europe,  UK") == "DK"
        assert country_code("UK, Germany") == "GB"


class TestDisplay:
    def test_a_code_becomes_a_readable_name(self):
        assert country_name("US") == "United States"
        assert country_name("gb") == "United Kingdom"

    def test_an_unknown_code_is_shown_as_itself_not_dropped(self):
        assert country_name("ZZ") == "ZZ"

    def test_nothing_at_all_reads_as_the_default(self):
        assert country_name(None) == "United States"
        assert country_name("") == "United States"

    def test_every_code_the_parser_can_emit_has_a_name(self):
        from app.services.job_country import ALIASES

        assert set(ALIASES) <= set(NAMES)

    def test_the_option_list_leads_with_the_default(self):
        codes = known_codes()
        assert codes[0] == DEFAULT
        assert len(codes) == len(NAMES)


class TestTheIntakeGate:
    """The switch that decides whether non-US listings are kept at all."""

    def _gate(self, mode, monkeypatch):
        from app.validators import remote_us

        monkeypatch.setattr(remote_us, "_load_rules", lambda: {"mode": mode})
        return remote_us.check_remote_us

    def test_strict_mode_still_rejects_a_foreign_listing(self, monkeypatch):
        check = self._gate("us_remote_strict", monkeypatch)
        assert check("Remote - Poland", "Remote role").ok is False

    def test_any_country_mode_keeps_it(self, monkeypatch):
        check = self._gate("remote_any_country", monkeypatch)
        assert check("Remote - Poland", "Remote role").ok is True

    def test_any_country_mode_still_insists_on_remote(self, monkeypatch):
        check = self._gate("remote_any_country", monkeypatch)
        result = check("Berlin, Germany", "Onsite only, five days a week")
        assert result.ok is False
        assert result.reason in {"not_remote", "hybrid_or_onsite"}

    def test_any_country_mode_still_rejects_onsite(self, monkeypatch):
        check = self._gate("remote_any_country", monkeypatch)
        assert check("Berlin, Germany", "", work_arrangement="onsite").ok is False

    def test_us_listings_keep_passing_in_both_modes(self, monkeypatch):
        for mode in ("us_remote_strict", "remote_any_country"):
            check = self._gate(mode, monkeypatch)
            assert check("Remote, US", "Remote position").ok is True, mode

    def test_an_unknown_mode_disables_the_gate_entirely(self, monkeypatch):
        check = self._gate("off", monkeypatch)
        assert check("Berlin, Germany", "Strictly onsite").ok is True
