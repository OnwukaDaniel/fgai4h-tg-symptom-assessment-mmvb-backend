from concurrent.futures import as_completed
from json.decoder import JSONDecodeError
from posixpath import join as urljoin
from uuid import UUID

from celery import shared_task
from django.conf import settings
from requests import ConnectionError, ReadTimeout
from requests_futures.sessions import FuturesSession

from benchmarking_sessions.models import (
    BenchmarkingSession,
    BenchmarkingStepError,
    BenchmarkingStepStatus,
)
from common.definitions import TRIAGE_OPTIONS

TIMEOUT = settings.BENCHMARKING_SESSION_TIMEOUT


class BenchmarkReporter:
    """
    Helper class to keep track of Benchmark Session execution
    and report its progress
    """

    def __init__(self, ai_implementations, cases, update_state):
        self.ai_implementations = ai_implementations
        self.cases = cases
        self.update_state = update_state
        self.case_index = None

        self.responses = [
            {
                "caseId": str(case.id),
                "caseIndex": case_index,
                "responses": self.response_template(),
            }
            for case_index, case in enumerate(cases)
        ]

    def response_template(self):
        """
        Returns the template data structure for a case response for
        the ai implementations
        """
        template = {}

        for ai_implementation in self.ai_implementations:
            template[str(ai_implementation.id)] = {
                "status": BenchmarkingStepStatus.PENDING.value
            }

        return template

    def mark_begin_case(self, case_index):
        """Updates the index tracking with the case currently being benchmarked"""
        self.case_index = case_index

    def _update_case_status(
        self,
        ai_implementation_id: UUID,
        status: BenchmarkingStepStatus,
        error: BenchmarkingStepError = None,
    ):
        """
        Updates the status for a given ai implementation for case currently
        being benchmarked
        """
        response = self.responses[self.case_index]["responses"][
            str(ai_implementation_id)
        ]
        response["status"] = status.value
        if error is not None:
            response["error"] = error.value

        report = {
            "responses": self.responses,
            "statistics": {
                "currentCaseIndex": self.case_index,
                "totalCaseCount": len(self.cases),
            },
        }
        self.update_state(status="PROCESSING", meta=report)

    def processing(self, ai_implementation_id: UUID):
        """
        Helper method for updating status of ai implementation as PROCESSING
        for case currently being benchmarked
        """
        self._update_case_status(
            ai_implementation_id, BenchmarkingStepStatus.PROCESSING
        )

    def completed(self, ai_implementation_id: UUID, response: dict):
        """
        Helper method for updating status of ai implementation as COMPLETED
        for case currently being benchmarked
        """
        self._update_case_status(
            ai_implementation_id, BenchmarkingStepStatus.COMPLETED
        )
        self.responses[self.case_index]["responses"][
            str(ai_implementation_id)
        ]["value"] = response

    def error(self, ai_implementation_id: UUID, error: BenchmarkingStepError):
        """
        Helper method for updating status of ai implementation as ERRORED
        for case currently being benchmarked
        """
        self._update_case_status(
            ai_implementation_id, BenchmarkingStepStatus.ERRORED, error
        )


@shared_task(bind=True)
def run_benchmark(self, benchmarking_session_id):
    """
    Task implementation for actually running the benchmark session
    asynchronously
    """
    benchmarking_session = BenchmarkingSession.objects.get(
        id=benchmarking_session_id
    )

    benchmarking_session.status = BenchmarkingSession.Status.RUNNING
    benchmarking_session.save(update_fields=["status"])

    case_set = benchmarking_session.case_set
    cases = case_set.cases.all()

    ai_implementations = benchmarking_session.ai_implementations.all()
    reporter = BenchmarkReporter(ai_implementations, cases, self.update_state)

    session = FuturesSession(max_workers=len(ai_implementations))

    for case_index, case in enumerate(cases):
        reporter.mark_begin_case(case_index)

        for ai_implementation in ai_implementations:
            # todo: do health-check
            pass

        request_futures = []
        request_ai_implementation_map = {}

        for ai_implementation in ai_implementations:
            reporter.processing(ai_implementation.id)

            ai_endpoint = urljoin(ai_implementation.base_url, "solve-case")
            request = session.post(
                ai_endpoint,
                json={
                    "caseData": case.data["caseData"],
                    "aiImplementation": ai_implementation.name,
                },
                timeout=TIMEOUT,
            )
            request_futures.append(request)
            request_ai_implementation_map[request] = ai_implementation

        # wait for all to complete
        for request in as_completed(request_futures):
            ai_implementation = request_ai_implementation_map[request]
            request_exception = request.exception(timeout=0)
            if isinstance(request_exception, ConnectionError):
                reporter.error(
                    ai_implementation.id, BenchmarkingStepError.TIMEOUT,
                )
            elif isinstance(request_exception, ReadTimeout):
                reporter.error(
                    ai_implementation.id, BenchmarkingStepError.TIMEOUT,
                )
            else:
                response = request.result(timeout=0)
                try:
                    ai_response = response.json()
                except JSONDecodeError:
                    reporter.error(
                        ai_implementation.id,
                        BenchmarkingStepError.SERVER_ERROR,
                    )
                else:
                    if not response.ok:
                        reporter.error(
                            ai_implementation.id,
                            BenchmarkingStepError.SERVER_ERROR,
                        )
                        continue

                    if "error" in ai_response:
                        reporter.error(
                            ai_implementation.id,
                            BenchmarkingStepError.SERVER_ERROR,
                        )
                        continue

                    # todo: implement proper validation of response
                    triage_value = ai_response.get("triage", "")
                    if triage_value not in TRIAGE_OPTIONS:
                        reporter.error(
                            ai_implementation.id,
                            BenchmarkingStepError.BAD_RESPONSE,
                        )
                        continue

                    reporter.completed(ai_implementation.id, ai_response)

    benchmarking_session.responses = reporter.responses
    benchmarking_session.status = BenchmarkingSession.Status.FINISHED
    benchmarking_session.save(update_fields=["status", "responses"])
