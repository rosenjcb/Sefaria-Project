
from config import *
from sefaria.model import *
from random import shuffle
from multiprocessing import Pool
import os
import inspect
import httplib
import base64
import json
import traceback
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# http://selenium-python.readthedocs.io/waits.html
# http://selenium-python.readthedocs.io/api.html#module-selenium.webdriver.support.expected_conditions
from selenium.webdriver.support.expected_conditions import title_contains, presence_of_element_located, staleness_of, element_to_be_clickable, visibility_of_element_located
from selenium.webdriver.common.keys import Keys


class AtomicTest(object):
    """
    Abstract Class
    AtomicTests are designed to be composed in any order, so as to test a wide range of orders of events
    A concrete AtomicTest implements the run method.
    """
    suite_key = None
    single_panel = True  # run this test on mobile?
    multi_panel = True  # run this test on desktop?

    every_build = False  # Run this test on every build?

    def __init__(self, driver, url):
        self.base_url = url
        self.driver = driver
        if not self.suite_key:
            raise Exception("Missing required variable - test_suite")
        if not self.multi_panel and not self.single_panel:
            raise Exception("Tests must run on at least one of mobile or desktop")

    def run(self):
        raise Exception("AtomicTest.run() needs to be defined for each test.")

    # Component methods
    def s2(self):
        self.driver.get(self.base_url + "/s2")
        WebDriverWait(self.driver, TEMPER).until(title_contains("Texts"))
        return self

    # TOC
    def load_toc(self):
        self.driver.get(self.base_url + "/texts")
        WebDriverWait(self.driver, TEMPER).until(title_contains("Texts"))
        return self

    def click_toc_category(self, category_name):
        self.driver.find_element_by_css_selector('.readerNavCategory[data-cat="{}"]'.format(category_name)).click()
        WebDriverWait(self.driver, TEMPER).until(title_contains(category_name))
        return self

    def click_toc_text(self, text_name):
        p1 = self.driver.find_element_by_css_selector('.refLink[data-ref^="{}"]'.format(text_name))
        p1.click()
        WebDriverWait(self.driver, TEMPER).until(title_contains(text_name))
        return self

    def click_toc_recent(self, tref, until=None):
        recent = self.driver.find_element_by_css_selector('.recentItem[data-ref="{}"]'.format(tref))
        recent.click()
        until = title_contains(tref) if until is None else until
        WebDriverWait(self.driver, TEMPER).until(until)

    # Text Panel
    # Todo: handle the case when the loaded page has different URL - because of scroll
    def load_ref(self, ref, filter=None):
        """
        takes string ref or object Ref
        :param ref:
        :param filter: "all", "Rashi", etc
        :return:
        """
        if isinstance(ref, basestring):
            ref = Ref(ref)
        assert isinstance(ref, Ref)
        url = self.base_url + "/" + ref.url()
        if filter is not None:
            url += "?with={}".format(filter)
        self.driver.get(url)
        if filter == "all":
            WebDriverWait(self.driver, TEMPER).until(presence_of_element_located((By.CSS_SELECTOR, ".categoryFilter")))
        elif filter is not None:
            # Filters load slower than the main page
            WebDriverWait(self.driver, TEMPER).until(presence_of_element_located((By.CSS_SELECTOR, ".filterSet > .textRange")))
        else:
            WebDriverWait(self.driver, TEMPER).until(presence_of_element_located((By.CSS_SELECTOR, ".textColumn")))
        return self

    #todo:
    def load_refs(self):
        pass

    def click_segment(self, ref):
        if isinstance(ref, basestring):
            ref = Ref(ref)
        assert isinstance(ref, Ref)
        segment = self.driver.find_element_by_css_selector('.segment[data-ref="{}"]'.format(ref.normal()))
        segment.click()
        # Todo: put a data-* attribute on .filterSet, for the multi-panel case
        WebDriverWait(self.driver, TEMPER).until(presence_of_element_located((By.CSS_SELECTOR, ".filterSet")))

    def scroll_to_segment(self, ref):
        if isinstance(ref, basestring):
            ref = Ref(ref)
        assert isinstance(ref, Ref)
        #todo

    # Connections Panel
    def find_text_filter(self, name):
        return self.driver.find_element_by_css_selector('.textFilter[data-name="{}"]'.format(name))

    def click_text_filter(self, name):
        f = self.find_text_filter(name)
        assert f, "Can not find text filter {}".format(name)
        f.click()
        WebDriverWait(self.driver, TEMPER).until(title_contains("with {}".format(name)))
        return self

    # Search
    def search_for(self, query):
        elem = self.driver.find_element_by_css_selector("input.search")
        elem.send_keys(query)
        elem.send_keys(Keys.RETURN)
        # todo: does this work for a second search?
        WebDriverWait(self.driver, TEMPER).until(presence_of_element_located((By.CSS_SELECTOR, ".result")))
        return self

    #Source Sheets
    def load_sheets(self):
        url = self.base_url + "/sheets"
        self.driver.get(url)
        WebDriverWait(self.driver, TEMPER).until(title_contains("Sheet"))

class TestResult(object):
    def __init__(self, test, cap, success, message=""):
        assert isinstance(test, AtomicTest) or inspect.isclass(cap)
        assert isinstance(success, bool)
        self.cap = cap
        self.test = test
        self.success = success
        self.message = message

    def __str__(self):
        return "{} - {} on {}{}".format(
            "Pass" if self.success else "Fail",
            self.test.__class__.__name__,
            Trial.cap_to_string(self.cap),
            ": {}".format(self.message) if self.message else ""
        )


class ResultSet(object):
    def __init__(self, results=None):
        """
        :param results: list of TestResult objects, or a list of lists
        :return:
        """
        self._aggregated = False
        self._test_results = [] if results is None else results
        assert (isinstance(t, TestResult) for t in self._test_results)
        self._indexed_tests = {}

    def __str__(self):
        return "\n".join([str(r) for r in self._test_results])

    def _aggregate(self):
        if not self._aggregated:
            for res in self._test_results:
                self._indexed_tests[(res.test.__class__, Trial.cap_to_short_string(res.cap))] = res.success
            self._aggregated = True

    def _results_as_matrix(self):
        self._aggregate()
        tests = list({res.test.__class__ for res in self._test_results})
        caps = list({Trial.cap_to_short_string(res.cap) for res in self._test_results})

        def text_result(test, cap):
            r = self._indexed_tests.get((test, cap), "N/A")
            if r is True:
                return "Pass"
            if r is False:
                return "Fail"
            return r

        results = [[test.__name__] + [text_result(test, cap) for cap in caps] for test in tests]
        results = [[""] + caps] + results
        return results

    def number_passed(self):
        return len([t for t in self._test_results if t.success])

    def number_failed(self):
        return len([t for t in self._test_results if not t.success])

    def report(self):
        ret = ""

        # http://stackoverflow.com/a/13214945/213042
        matrix = self._results_as_matrix()
        s = [[str(e) for e in row] for row in matrix]
        lens = [max(map(len, col)) for col in zip(*s)]
        fmt = ' '.join('{{:{}}}'.format(x) for x in lens)
        table = [fmt.format(*row) for row in s]
        ret += '\n'.join(table)

        total_tests = len(self._test_results)
        passed_tests = self.number_passed()
        percentage_passed = (float(passed_tests) / total_tests) * 100
        ret += "\n\n{}/{} - {:.0f}% passed".format(passed_tests, total_tests, percentage_passed)
        return ret

    def include(self, result):
        self._aggregated = False
        if isinstance(result, TestResult):
            self._test_results.append(result)
        elif isinstance(result, list):
            for res in result:
                self.include(res)


class Trial(object):
    default_local_driver = webdriver.Chrome

    def __init__(self, platform="local", build=None, tests=None, caps=None, parallel=None):
        """
        :param caps: If local: webdriver classes, if remote, dictionaries of capabilities
        :param platform: "sauce", "bstack", "local", "travis"
        :return:
        """
        assert platform in ["sauce", "bstack", "local", "travis"]
        if platform == "travis":
            global SAUCE_USERNAME, SAUCE_ACCESS_KEY
            SAUCE_USERNAME = os.getenv('SAUCE_USERNAME')
            SAUCE_ACCESS_KEY = os.getenv('SAUCE_ACCESS_KEY')
            self.BASE_URL = LOCAL_URL
            self.caps = caps if caps else SAUCE_CORE_CAPS
            for cap in self.caps:
                cap["tunnelIdentifier"] = os.getenv('TRAVIS_JOB_NUMBER')
            self.tests = get_every_build_tests(get_atomic_tests()) if tests is None else tests
            self.is_local = False
            platform = "sauce"  # After this initial setup - use the sauce platform
        elif platform == "local":
            self.is_local = True
            self.BASE_URL = LOCAL_URL
            self.caps = caps if caps else [self.default_local_driver]
            self.tests = get_atomic_tests() if tests is None else tests
        else:
            self.is_local = False
            self.BASE_URL = REMOTE_URL
            self.caps = caps if caps else SAUCE_CAPS if platform == "sauce" else BS_CAPS
            self.tests = get_atomic_tests() if tests is None else tests
        self.platform = platform
        self.build = build
        self._results = ResultSet()
        self.parallel = parallel if parallel is not None else False if self.is_local else True
        if self.parallel:
            self.thread_count = BS_MAX_THREADS if self.platform == "bstack" else SAUCE_MAX_THREADS

    def _get_driver(self, cap=None):
        """
        :param cap: If remote, cap is a dictionary of capabilities.
                    If local, it's a webdriver class
        :return:
        """
        if self.platform == "local":
            cap = cap if cap else self.default_local_driver
            driver = cap()
        elif self.platform == "sauce":
            assert cap is not None
            driver = webdriver.Remote(
                command_executor='http://{}:{}@ondemand.saucelabs.com:80/wd/hub'.format(SAUCE_USERNAME, SAUCE_ACCESS_KEY),
                desired_capabilities=cap)
        elif self.platform == "bstack":
            assert cap is not None
            driver = webdriver.Remote(
                command_executor='http://{}:{}@hub.browserstack.com:80/wd/hub'.format(BS_USER, BS_KEY),
                desired_capabilities=cap)
        else:
            raise Exception("Unrecognized platform: {}".format(self.platform))

        #todo: better way to do this?
        #driver.get(self.BASE_URL + "/s2")
        return driver

    def _run_one_atomic_test(self, driver, test_class, cap):
        """
        :param test_class:
        :return:
        """
        name = "{} / {}".format(test_class.__name__, Trial.cap_to_string(cap))
        print "{} - Starting".format(name)
        assert issubclass(test_class, AtomicTest)
        test = test_class(driver, self.BASE_URL)
        try:
            driver.execute_script('"**** Enter {} ****"'.format(test))
            test.run()
            driver.execute_script('"**** Exit {} ****"'.format(test))
        except Exception as e:
            msg = getattr(e, "message", None) or getattr(e, "msg", None)
            print "{} - Failed".format(name)
            traceback.print_exc()
            return TestResult(test, cap, False, msg)
        else:
            print "{} - Passed".format(name)
            return TestResult(test, cap, True)

    def _test_one(self, test, cap):
        driver = None
        try:
            if self.is_local:
                mode = "multi_panel"   # Assuming that local isn't single panel
            else:
                mode = cap.get("sefaria_mode")
                cap.update({
                    'name': "{} on {}".format(test.__name__, self.cap_to_string(cap)),
                    'build': self.build,
                })
            if (mode == "multi_panel" and not test.multi_panel) or (mode == "single_panel" and not test.single_panel):
                return None
            driver = self._get_driver(cap)
            result = self._run_one_atomic_test(driver, test, cap)
            if self.platform == "sauce":
                self.set_sauce_result(driver, result.success)
            driver.quit()
            return result
        except Exception as e:
            if driver is not None:
                driver.quit()
            name = "{} / {}".format(test.__name__, Trial.cap_to_string(cap))
            msg = getattr(e, "message", None) or getattr(e, "msg", None)
            print "{} - Aborted".format(name)
            traceback.print_exc()
            return TestResult(test, cap, False, msg)

    def _test_on_all(self, test):
        """
        Given a test, test it on all browsers
        :param test:
        :return:
        """
        if self.parallel:
            p = Pool(self.thread_count)
            l = len(self.caps)
            tresults = p.map(_test_one_worker, zip([self]*l, [test]*l, self.caps))
        else:
            tresults = []
            for cap in self.caps:
                tresults.append(self._test_one(test, cap))

        return [t for t in tresults if t is not None]

    def run(self):
        for test in self.tests:
            self._results.include(self._test_on_all(test))
        return self

    def results(self):
        return self._results

    @staticmethod
    def set_sauce_result(driver, result):
        base64string = base64.encodestring('%s:%s' % (SAUCE_USERNAME, SAUCE_ACCESS_KEY))[:-1]

        def set_test_status(jobid, passed=True):
            body_content = json.dumps({"passed": passed})
            connection = httplib.HTTPConnection("saucelabs.com")
            connection.request('PUT', '/rest/v1/%s/jobs/%s' % (SAUCE_USERNAME, jobid),
                               body_content,
                               headers={"Authorization": "Basic %s" % base64string})
            result = connection.getresponse()
            return result.status == 200

        set_test_status(driver.session_id, passed=result)
        return result

    @staticmethod
    def cap_to_string(cap):
        if inspect.isclass(cap):
            return cap.__module__.split(".")[-2]
        return (cap.get("deviceName") or  # sauce mobile
                cap.get("device") or  # browserstack mobile
                ("{} {} on {} {}".format(cap.get("browser"), cap.get("browser_version"), cap.get("os"), cap.get("os_version")) if cap.get("browser") else  # browserstack desktop
                "{} {} on {}".format(cap.get('browserName'), cap.get("version"), cap.get('platform'))))  # sauce desktop

    @staticmethod
    def cap_to_short_string(cap):
        if inspect.isclass(cap):
            return cap.__module__.split(".")[-2]
        return cap.get("sefaria_short_name")


#  This function is used to get around the limitations of multiprocessing.Pool.map - that it will not take a method as first argument
#  http://www.rueckstiess.net/research/snippets/show/ca1d7d90
def _test_one_worker(arg, **kwargs):
    return Trial._test_one(*arg, **kwargs)


def get_subclasses(c):
    subclasses = c.__subclasses__()
    for d in list(subclasses):
        subclasses.extend(get_subclasses(d))

    return subclasses


def get_atomic_tests():
    return get_subclasses(AtomicTest)


def get_test_suite_keys():
    return list(set([t.suite_key for t in get_atomic_tests()]))


def get_tests_in_suite(key):
    return [t for t in get_atomic_tests() if t.suite_key == key]


def get_mobile_tests(tests):
    return [t for t in tests if t.mobile]


def get_desktop_tests(tests):
    return [t for t in tests if t.desktop]


def get_multiplatform_tests(tests):
    return [t for t in tests if t.desktop and t.mobile]


def get_every_build_tests(tests):
    return [t for t in tests if t.every_build]


'''
    def test_local(self):
        tests = get_atomic_tests()
        shuffle(tests)
        driver = self._get_driver()

        # Insure that we're on s2
        driver.get(LOCAL_URL + "/s2")

        print ", ".join([test.__name__ for test in tests])

        for test_class in tests:
            test = test_class(LOCAL_URL)
            test.run(driver)
        driver.quit()


    def _test_all(self, build):
        p = Pool(MAX_THREADS)
        for key in get_test_suite_keys():
            tests = get_tests_in_suite(key)
            shuffle(tests)

            if not caps:
                for cap in default_desktop_caps:
                    cap.update({
                        'name': "{} on {}".format(key, cap_to_string(cap)),
                        'build': build,
                        'tests': get_desktop_tests(tests),
                        "testing_platform": platform_string
                    })
                for cap in default_mobile_caps:
                    cap.update({
                        'name': "{} on {}".format(key, cap_to_string(cap)),
                        'build': build,
                        'tests': get_mobile_tests(tests),
                        "testing_platform": platform_string
                    })
                caps = default_desktop_caps + default_mobile_caps
            else:
                for cap in caps:
                    cap.update({
                        'name': "{} on {}".format(key, cap_to_string(cap)),
                        'build': build,
                        'tests': tests,  # Lazy assumption that all tests are run if caps are provided as an arg.
                        "testing_platform": platform_string
                    })
            results = p.map(_test_on_one_browser, caps)
            print "\n\nSuite: {}".format(key)
            print "\n".join(results)
'''


'''
def _test_each_on(self, cap):
    """
    Given a browser, test every test on that browser
    :param cap:
    :return:
    """
    results = []
    for test in self.tests:
        # TODO: Mobile / Desktop
        driver = self._get_driver(cap)
        results.append(self._run_one_atomic_test(driver, test))
        driver.quit()
    return results
'''
