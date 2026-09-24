import unittest

from crm import contact_normalization as module


class ApolloNormalizationTests(unittest.TestCase):
    def test_linkedin_transport_country_and_tracking(self):
        self.assertEqual(
            module.norm("linkedin", "http://www.linkedin.com/in/Example/"),
            module.norm("linkedin", "https://uk.linkedin.com/in/example?trk=foo"),
        )

    def test_x_alias(self):
        self.assertEqual(
            module.norm("x", "https://twitter.com/Example/"),
            module.norm("x", "https://x.com/example"),
        )

    def test_distinct_profiles_stay_distinct(self):
        self.assertNotEqual(
            module.norm("linkedin", "https://linkedin.com/in/agnay"),
            module.norm("linkedin", "https://linkedin.com/in/agnaysrivastava"),
        )

    def test_email_case_and_distinct_domains(self):
        self.assertEqual(module.norm("email", " Person@Example.com "), "person@example.com")
        self.assertNotEqual(
            module.norm("email", "person@one.com"), module.norm("email", "person@two.com")
        )
