import uuid
from django.test import TestCase, override_settings
from kafka import KafkaConsumer
from kafka.common import KafkaUnavailableError
from casexml.apps.case.mock import CaseBlock
from casexml.apps.case.signals import case_post_save
from casexml.apps.case.util import post_case_blocks
from corehq.apps.change_feed import topics
from corehq.apps.change_feed.consumer.feed import change_meta_from_kafka_message
from corehq.apps.es import CaseES
from corehq.form_processor.tests.utils import FormProcessorTestUtils
from corehq.pillows.case import CasePillow
from corehq.util.context_managers import drop_connected_signals
from corehq.util.elastic import delete_es_index
from corehq.util.test_utils import trap_extra_setup
from testapps.test_pillowtop.utils import get_test_kafka_consumer


class CasePillowTest(TestCase):

    domain = 'case-pillowtest-domain'

    def setUp(self):
        FormProcessorTestUtils.delete_all_cases()
        self.pillow = CasePillow()
        self.elasticsearch = self.pillow.get_es_new()
        delete_es_index(self.pillow.es_index)

    def tearDown(self):
        delete_es_index(self.pillow.es_index)

    def test_case_pillow_couch(self):
        # make a case
        case_id = uuid.uuid4().hex
        case_name = 'case-name-{}'.format(uuid.uuid4().hex)
        case = self._make_a_case(case_id, case_name)

        # send to elasticsearch
        self.pillow.process_changes(since=0, forever=False)
        self.elasticsearch.indices.refresh(self.pillow.es_index)

        # verify there
        results = CaseES().run()
        self.assertEqual(1, results.total)
        case_doc = results.hits[0]
        self.assertEqual(self.domain, case_doc['domain'])
        self.assertEqual(case_id, case_doc['_id'])
        self.assertEqual(case_name, case_doc['name'])

        # cleanup
        case.delete()

    @override_settings(TESTS_SHOULD_USE_SQL_BACKEND=True)
    def test_case_pillow_sql(self):
        consumer = get_test_kafka_consumer(topics.CASE_SQL)
        # have to get the seq id before the change is processed
        kafka_seq = consumer.offsets()['fetch'][(topics.CASE_SQL, 0)]

        # make a case
        case_id = uuid.uuid4().hex
        case_name = 'case-name-{}'.format(uuid.uuid4().hex)
        case = self._make_a_case(case_id, case_name)

        # confirm change made it to kafka
        message = consumer.next()
        change_meta = change_meta_from_kafka_message(message.value)
        self.assertEqual(case.case_id, change_meta.document_id)
        self.assertEqual(self.domain, change_meta.domain)

        # todo: send to elasticsearch
        # sql_pillow = get_sql_xform_to_elasticsearch_pillow()
        # sql_pillow.process_changes(since=kafka_seq, forever=False)
        # self.elasticsearch.indices.refresh(self.pillow.es_index)

        # confirm change made it to elasticserach
        results = CaseES().run()
        self.assertEqual(1, results.total)
        case_doc = results.hits[0]
        self.assertEqual(self.domain, case_doc['domain'])
        self.assertEqual(case_id, case_doc['_id'])
        self.assertEqual(case_name, case_doc['name'])

    def _make_a_case(self, case_id, case_name):
        # this avoids having to deal with all the reminders code bootstrap
        with drop_connected_signals(case_post_save):
            form, cases = post_case_blocks(
                [
                    CaseBlock(
                        create=True,
                        case_id=case_id,
                        case_name=case_name,
                    ).as_xml()
                ], domain=self.domain
            )
        self.assertEqual(1, len(cases))
        return cases[0]
