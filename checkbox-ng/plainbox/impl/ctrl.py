# This file is part of Checkbox.
#
# Copyright 2013 Canonical Ltd.
# Written by:
#   Zygmunt Krynicki <zygmunt.krynicki@canonical.com>
#
# Checkbox is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3,
# as published by the Free Software Foundation.
#
# Checkbox is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Checkbox.  If not, see <http://www.gnu.org/licenses/>.

"""
:mod:`plainbox.impl.ctrl` -- Controller Classes
===============================================

Session controller classes implement the glue between models (jobs, test plans,
session state) and the rest of the application. They encapsulate knowledge that
used to be special-cased and sprinkled around various parts of both plainbox
and particular plainbox-using applications.

Execution controllers are used by the :class:`~plainbox.impl.runner.JobRunner`
class to select the best method to execute a command of a particular job.  This
is mostly applicable to jobs that need to run as another user, typically as
root, as the method that is used to effectively gain root differs depending on
circumstances.
"""

try:
    import grp
except ImportError:
    grp = None
import itertools
import json
import logging
import os
from functools import partial

from plainbox.abc import IJobResult, ISessionStateController
from plainbox.i18n import gettext as _
from plainbox.impl.depmgr import DependencyType
from plainbox.impl.resource import (
    ExpressionCannotEvaluateError,
    ExpressionFailedError,
    ResourceProgramError,
    Resource,
)
from plainbox.impl.secure.origin import JobOutputTextSource
from plainbox.impl.secure.rfc822 import RFC822SyntaxError, gen_rfc822_records
from plainbox.impl.session.jobs import InhibitionCause, JobReadinessInhibitor
from plainbox.impl.unit.template import TemplateUnit
from plainbox.impl.unit.unit import MissingParam
from plainbox.impl.validation import Severity
from plainbox.suspend_consts import Suspend
from plainbox.impl.unit.job import InvalidJob

__all__ = [
    "CheckBoxSessionStateController",
    "checkbox_session_state_ctrl",
]


logger = logging.getLogger("plainbox.ctrl")


class CheckBoxSessionStateController(ISessionStateController):
    """
    A combo controller for CheckBox-like jobs.

    This controller implements the following features:

        * A job may depend on another job, this is expressed via the 'depends'
          attribute. Cyclic dependencies are not allowed. A job will become
          inhibited if any of its dependencies have outcome other than
          OUTCOME_PASS
        * A job may require that a particular resource expression evaluates to
          true. This is expressed via the 'requires' attribute. A job will
          become inhibited if any of the requirement programs evaluates to
          value other than True.
        * A job may have the attribute 'plugin' equal to "resource" which will
          cause the controller to interpret the stdout of the command as a set
          of resource definitions.
    """

    def get_dependency_set(self, job, job_list=None):
        """
        Get the set of direct dependencies of a particular job.

        :param job:
            A IJobDefinition instance that is to be visited
        :param job_list:
            List of jobs to check dependencies from
        :returns:
            set of pairs (dep_type, job_id)

        Returns a set of pairs (dep_type, job_id) that describe all
        dependencies of the specified job. The first element in the pair,
        dep_type, is a DependencyType. The second element is the id of the job.
        """
        depends = DependencyType.DEPENDS
        after = DependencyType.AFTER
        resource = DependencyType.RESOURCE
        before = DependencyType.BEFORE
        direct_deps = job.get_direct_dependencies()
        after_deps = job.get_after_dependencies()
        # Add the jobs that have this job referenced in their "before" field.
        before_refs = job.before_references

        try:
            resource_deps = job.get_resource_dependencies()
        except ResourceProgramError:
            resource_deps = ()

        # This step is here to add the dependencies to the suspend jobs.
        suspend_job_id_list = [
            Suspend.AUTO_JOB_ID,
            Suspend.MANUAL_JOB_ID,
        ]
        if job.id in suspend_job_id_list:
            suspend_deps = self._get_before_suspend_dependency_set(
                job.id, job_list
            )
        else:
            suspend_deps = set()

        result = set(
            itertools.chain(
                zip(itertools.repeat(depends), direct_deps),
                zip(itertools.repeat(resource), resource_deps),
                zip(itertools.repeat(after), after_deps),
                zip(itertools.repeat(after), suspend_deps),
                zip(itertools.repeat(before), before_refs),
            )
        )
        return result

    def add_before_deps(self, job, job_map, global_job_map):
        """
        Add all "before" references declared in a job to the corresponding
        jobs as an "after" dependency in the before_references set.

        If a job (B) has a "before" field, we add this job as an "after"
        dependency to the job (A).

        id: A          id: A
                   ->  after: B
        id: B      ->
        before: A      id: B
        """
        before_deps = job.get_before_dependencies()
        for dep_id in before_deps:
            # Check if the dep_id is a valid job
            if dep_id not in global_job_map:
                logger.error(
                    "Job {} has a before dependency on {} which does not "
                    "exist".format(job.id, dep_id)
                )
            elif dep_id not in job_map:
                logger.debug(
                    "Job {} has a before dependency on {} which is not "
                    "in the current test plan".format(job.id, dep_id)
                )
            else:
                job_map[dep_id].before_references.add(job.id)

    def _get_before_suspend_dependency_set(self, suspend_job_id, job_list):
        """
        Get the set of after dependencies of a suspend job.

        Jobs that have a ``also-after-suspend[-manual]`` flag should be run
        before their associated suspend job. Similarly, jobs that declare a
        sibling with a dependency on a suspend job should be run before said
        suspend job. This function finds these jobs and add them as a
        dependency for their associated suspend job.

        :param suspend_job_id:
            The id of a suspend job. One of the following is expected:
            Suspend.AUTO_JOB_ID or Suspend.MANUAL_JOB_ID.
        :param job_list:
            List of jobs to search dependencies on.
        :returns:
            A set of job ids that need to be run before the suspend job
        """
        p_suspend_job_id = partial(
            self._is_job_impacting_suspend, suspend_job_id
        )
        suspend_deps_jobs = filter(p_suspend_job_id, job_list)
        suspend_deps = set(job.id for job in suspend_deps_jobs)
        return suspend_deps

    def _is_job_impacting_suspend(self, suspend_job_id, job):
        """
        Check if the ``suspend_job_id`` job needs to be run after a given
        ``job``. This is the case if the ``job`` has a "also after suspend"
        flag, or if it defines a sibling that has a dependency on the suspend
        job.
        """
        expected_flag = {
            Suspend.AUTO_JOB_ID: Suspend.AUTO_FLAG,
            Suspend.MANUAL_JOB_ID: Suspend.MANUAL_FLAG,
        }.get(suspend_job_id)
        if job.flags and expected_flag in job.flags:
            return True
        if job.siblings:
            for sibling_data in json.loads(job.tr_siblings()):
                if suspend_job_id in sibling_data.get("depends", []):
                    return True
        return False

    def get_inhibitor_list(self, session_state, job):
        """
        Get a list of readiness inhibitors that inhibit a particular job.

        :param session_state:
            A SessionState instance that is used to interrogate the
            state of the session where it matters for a particular
            job. Currently this is used to access resources and job
            results.
        :param job:
            A JobDefinition instance
        :returns:
            List of JobReadinessInhibitor
        """
        inhibitors = []
        # Check if all job resource requirements are met
        prog = job.get_resource_program()
        if prog is not None:
            try:
                prog.evaluate_or_raise(session_state.resource_map)
            except ExpressionCannotEvaluateError as exc:
                for resource_id in exc.expression.resource_id_list:
                    if (
                        session_state.job_state_map[resource_id].result.outcome
                        == "pass"
                    ):
                        continue
                    # Lookup the related job (the job that provides the
                    # resources needed by the expression that cannot be
                    # evaluated)
                    related_job = session_state.job_state_map[resource_id].job
                    # Add A PENDING_RESOURCE inhibitor as we are unable to
                    # determine if the resource requirement is met or not. This
                    # can happen if the resource job did not ran for any reason
                    # (it can either be prevented from running by normal means
                    # or simply be on the run_list but just was not executed
                    # yet).
                    inhibitor = JobReadinessInhibitor(
                        cause=InhibitionCause.PENDING_RESOURCE,
                        related_job=related_job,
                        related_expression=exc.expression,
                    )
                    inhibitors.append(inhibitor)
            except ExpressionFailedError as exc:
                # When expressions fail then all the associated resources are
                # marked as failed since we don't want to get into the analysis
                # of logic expressions to know any "better".
                for resource_id in exc.expression.resource_id_list:
                    # Lookup the related job (the job that provides the
                    # resources needed by the expression that failed)
                    related_job = session_state.job_state_map[resource_id].job
                    # Add a FAILED_RESOURCE inhibitor as we have all the data
                    # to run the requirement program but it simply returns a
                    # non-True value. This typically indicates a missing
                    # software package or necessary hardware.
                    inhibitor = JobReadinessInhibitor(
                        cause=InhibitionCause.FAILED_RESOURCE,
                        related_job=related_job,
                        related_expression=exc.expression,
                    )
                    inhibitors.append(inhibitor)
        # Check if all job dependencies ran successfully
        for dep_id in sorted(job.get_direct_dependencies()):
            dep_job_state = session_state.job_state_map[dep_id]
            # If the dependency did not have a chance to run yet add the
            # PENDING_DEP inhibitor.
            if dep_job_state.result.outcome == IJobResult.OUTCOME_NONE:
                inhibitor = JobReadinessInhibitor(
                    cause=InhibitionCause.PENDING_DEP,
                    related_job=dep_job_state.job,
                )
                inhibitors.append(inhibitor)
            # If the dependency is anything but successful add the
            # FAILED_DEP inhibitor. In theory the PENDING_DEP code above
            # could be discarded but this would loose context and would
            # prevent the operator from actually understanding why a job
            # cannot run.
            elif dep_job_state.result.outcome != IJobResult.OUTCOME_PASS:
                inhibitor = JobReadinessInhibitor(
                    cause=InhibitionCause.FAILED_DEP,
                    related_job=dep_job_state.job,
                )
                inhibitors.append(inhibitor)
        # Check if all "after" dependencies ran yet
        # TODO: If we get rid of the "pulling" behavior of after dependencies,
        # we could remove this loop.
        for dep_id in sorted(job.get_after_dependencies()):
            dep_job_state = session_state.job_state_map[dep_id]
            # If the dependency did not have a chance to run yet add the
            # PENDING_DEP inhibitor.
            if dep_job_state.result.outcome == IJobResult.OUTCOME_NONE:
                inhibitor = JobReadinessInhibitor(
                    cause=InhibitionCause.PENDING_DEP,
                    related_job=dep_job_state.job,
                )
                inhibitors.append(inhibitor)
        for dep_id in sorted(job.get_salvage_dependencies()):
            dep_job_state = session_state.job_state_map[dep_id]
            if dep_job_state.result.outcome != IJobResult.OUTCOME_FAIL:
                inhibitor = JobReadinessInhibitor(
                    cause=InhibitionCause.NOT_FAILED_DEP,
                    related_job=dep_job_state.job,
                )
                inhibitors.append(inhibitor)
        if job.id in [Suspend.AUTO_JOB_ID, Suspend.MANUAL_JOB_ID]:
            for inhibitor in self._get_suspend_inhibitor_list(
                session_state, job
            ):
                inhibitors.append(inhibitor)
        return inhibitors

    def _get_suspend_inhibitor_list(self, session_state, suspend_job):
        """
        Get a list of readiness inhibitors that inhibit a suspend job.

        Jobs that have a ``also-after-suspend[-manual]`` flag should be run
        before their associated suspend job. Similary, jobs that declare a
        sibling with a dependency on a suspend job should be run before said
        suspend job. This function finds these jobs and add them as a
        inhibitor for their associated suspend job.

        :param session_state:
            A SessionState instance that is used to interrogate the
            state of the session where it matters for a particular
            job. Currently this is used to access resources and job
            results.
        :param suspend_job:
            A suspend job.
        :returns:
            List of JobReadinessInhibitor
        """
        suspend_inhibitors = []
        undesired_inhibitor = JobReadinessInhibitor(
            cause=InhibitionCause.UNDESIRED
        )
        # We are only interested in jobs that are actually going to run
        run_list = [
            state.job
            for state in session_state.job_state_map.values()
            if undesired_inhibitor not in state.readiness_inhibitor_list
        ]
        p_suspend_job_id = partial(
            self._is_job_impacting_suspend, suspend_job.id
        )
        suspend_inhibitors_jobs = filter(p_suspend_job_id, run_list)
        for job in suspend_inhibitors_jobs:
            if (
                session_state.job_state_map[job.id].result.outcome
                == IJobResult.OUTCOME_NONE
            ):
                inhibitor = JobReadinessInhibitor(
                    cause=InhibitionCause.PENDING_DEP,
                    related_job=job,
                )
                suspend_inhibitors.append(inhibitor)
        return suspend_inhibitors

    def observe_result(self, session_state, job, result, fake_resources=False):
        """
        Notice the specified test result and update readiness state.

        :param session_state:
            A SessionState object
        :param job:
            A JobDefinition object
        :param result:
            A IJobResult object
        :param fake_resources:
            An optional parameter to trigger test plan export execution mode
            using fake resourceobjects

        This function updates the internal result collection with the data from
        the specified test result. Results can safely override older results.
        Results also change the ready map (jobs that can run) because of
        dependency relations.

        Some results have deeper meaning, those are results for resource jobs.
        They are discussed in detail below:

        Resource jobs produce resource records which are used as data to run
        requirement expressions against. Each time a result for a resource job
        is presented to the session it will be parsed as a collection of RFC822
        records. A new entry is created in the resource map (entirely replacing
        any old entries), with a list of the resources that were parsed from
        the IO log.
        """
        # Store the result in job_state_map
        session_state.job_state_map[job.id].result = result
        session_state.on_job_state_map_changed()
        session_state.on_job_result_changed(job, result)
        # Treat some jobs specially and interpret their output
        if job.plugin == "resource":
            self._process_resource_result(
                session_state, job, result, fake_resources
            )

    def _process_resource_result(
        self, session_state, job, result, fake_resources=False
    ):
        """
        Analyze a result of a CheckBox "resource" job and generate
        or replace resource records.
        """
        self._parse_and_store_resource(session_state, job, result)
        if session_state.resource_map[job.id] != [Resource({})]:
            self._instantiate_templates(
                session_state, job, result, fake_resources
            )

    def _parse_and_store_resource(self, session_state, job, result):
        # NOTE: https://bugs.launchpad.net/checkbox/+bug/1297928
        # If we are resuming from a session that had a resource job that
        # never ran, we will see an empty MemoryJobResult object.
        # Processing empty I/O log would create an empty resource list
        # and that state is different from the state the session started
        # before it was suspended, so don't
        if result.outcome is IJobResult.OUTCOME_NONE:
            return
        new_resource_list = []
        for record in gen_rfc822_records_from_io_log(job, result):
            # XXX: Consider forwarding the origin object here.  I guess we
            # should have from_frc822_record as with JobDefinition
            resource = Resource(record.data)
            logger.debug(_("Storing resource record %r: %s"), job.id, resource)
            new_resource_list.append(resource)
        # Create an empty resource object to properly fail __getattr__ calls
        if not new_resource_list:
            new_resource_list = [Resource({})]
        # Replace any old resources with the new resource list
        session_state.set_resource_list(job.id, new_resource_list)

    @staticmethod
    def _filter_invalid_log(unit):
        """
        Used to filter units that are generated but are invalid, will return
        True if the unit has to be kept, False if it has to be discarded
        """
        try:
            check_result = unit.check()
        except MissingParam as m:
            logger.critical(
                _("Ignoring %s with missing template parameter %s"),
                unit._raw_data.get("id"),
                m.parameter,
            )
            return False

        errors = [c for c in check_result if c.severity == Severity.error]
        warnings = (c for c in check_result if c.severity == Severity.warning)
        for warning in warnings:
            logger.warning(str(warning))

        if not errors:
            return True

        for error in errors:
            logger.error(str(error))
        logger.critical("Ignoring invalid generated job %s", unit.id)
        return False

    @staticmethod
    def _wrap_invalid_units(unit):
        """
        Used to wrap invalid units generated by the template
        """

        try:
            check_result = unit.check()
            errors = [c for c in check_result if c.severity == Severity.error]
            warnings = (
                c for c in check_result if c.severity == Severity.warning
            )
            for warning in warnings:
                logger.warning(str(warning))
        except MissingParam as m:
            errors = [m]
        if not errors:
            return unit
        return InvalidJob.from_unit(unit, errors=errors)

    def _instantiate_templates(
        self, session_state, job, result, fake_resources=False
    ):
        # NOTE: https://bugs.launchpad.net/checkbox/+bug/1297928
        # If we are resuming from a session that had a resource job that
        # never ran, we will see an empty MemoryJobResult object.
        # Processing empty I/O log would create an empty resource list
        # and that state is different from the state the session started
        # before it was suspended, so don't
        if result.outcome is IJobResult.OUTCOME_NONE:
            return
        # get all templates that use this (resource) job as template_resource
        template_units = filter(
            lambda unit: isinstance(unit, TemplateUnit)
            and unit.resource_id == job.id,
            session_state.unit_list,
        )
        # get the parsed resource (list of dict created from the resource
        # stdout)
        parsed_resource = session_state.resource_map[job.id]
        # get a list of all new units generated from each template
        # this is an array of arrays units as follows:
        # [[unit_from_template1, ...], [unit_from_template2, ...]]
        new_units_lists = (
            template_unit.instantiate_all(parsed_resource, fake_resources)
            for template_unit in template_units
        )
        # flattening list to make it easier to work with
        new_units = (
            new_unit
            for new_unit_list in new_units_lists
            for new_unit in new_unit_list
        )

        if (
            session_state.metadata.FLAG_FEATURE_STRICT_TEMPLATE_EXPANSION
            in session_state.metadata.flags
        ):

            new_units = map(self._wrap_invalid_units, new_units)
        else:
            new_units = filter(self._filter_invalid_log, new_units)

        for new_unit in new_units:
            # here they are added unconditionally as they will be checked
            # before running to make error reporting possible or were already
            # filtered by non-strict template expansion
            session_state.add_unit(new_unit, via=job, recompute=False)
        session_state._recompute_job_readiness()


def gen_rfc822_records_from_io_log(job, result):
    """
    Convert io_log from a job result to a sequence of rfc822 records
    """
    logger.debug(_("processing output from a job: %r"), job)
    # Select all stdout lines from the io log
    line_gen = (
        record[2].decode("UTF-8", errors="replace")
        for record in result.get_io_log()
        if record[1] == "stdout"
    )
    # Allow the generated records to be traced back to the job that defined
    # the command which produced (printed) them.
    source = JobOutputTextSource(job)
    try:
        # Parse rfc822 records from the subsequent lines
        for record in gen_rfc822_records(line_gen, source=source):
            yield record
    except RFC822SyntaxError as exc:
        # When this exception happens we will _still_ store all the
        # preceding records. This is worth testing

        logger.warning(
            # TRANSLATORS: keep the word "local" untranslated. It is a
            # special type of job that needs to be distinguished.
            _("local script %s returned invalid RFC822 data: %s"),
            job.id,
            exc,
        )


checkbox_session_state_ctrl = CheckBoxSessionStateController()


class SymLinkNest:
    """
    A class for setting up a control directory with symlinked executables
    """

    def __init__(self, dirname):
        self._dirname = dirname

    def add_provider(self, provider):
        """
        Add all of the executables associated a particular provider

        :param provider:
            A Provider1 instance
        """
        for filename in provider.executable_list:
            self.add_executable(filename)

    def add_executable(self, filename):
        """
        Add a executable to the control directory
        """
        logger.debug(
            _("Adding executable %s to nest %s"), filename, self._dirname
        )
        dest = os.path.join(self._dirname, os.path.basename(filename))
        try:
            os.symlink(filename, dest)
        except OSError as exc:
            # Allow symlinks to fail on Windows where it requires some
            # untold voodoo magic to work (aka running as root)
            logger.error(
                _("Unable to create symlink s%s -> %s: %r"),
                filename,
                dest,
                exc,
            )
