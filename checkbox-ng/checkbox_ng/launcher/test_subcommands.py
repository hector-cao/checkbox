# This file is part of Checkbox.
#
# Copyright 2023 Canonical Ltd.
# Written by:
#   Massimiliano Girardi <massimiliano.girardi@canonical.com>
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

import textwrap
import datetime

from functools import partial
from unittest import TestCase

from unittest.mock import patch, Mock, MagicMock, mock_open

from io import StringIO

from plainbox.impl.unit.job import JobDefinition
from plainbox.impl.unit.template import TemplateUnit

from checkbox_ng.launcher.subcommands import (
    Run,
    Expand,
    List,
    Launcher,
    ListBootstrapped,
    IncompatibleJobError,
    ResumeInstead,
    IJobResult,
    request_comment,
    generate_resume_candidate_description,
    get_testplan_id_by_id,
    print_objs,
)
from checkbox_ng.urwid_ui import ManifestBrowser


class TestSharedFunctions(TestCase):
    def make_unit_mock(self, **kwargs):
        to_r = MagicMock(**kwargs)
        try:
            # name is a kwarg of mock, so we need to set it manually
            to_r.name = kwargs["name"]
        except KeyError:
            pass
        return to_r

    def get_test_tree(self):
        # made this uniform as the function should be able to handle any valid
        # unit tree
        return self.make_unit_mock(
            group="service",
            children=[
                self.make_unit_mock(
                    group="exporter",
                    children=None,
                    name="exporter name",
                    attrs={"id": "exporter id"},
                ),
                self.make_unit_mock(
                    group="job",
                    name="job name",
                    children=None,
                    attrs={"id": "job id"},
                ),
                self.make_unit_mock(
                    group="template",
                    name="template name",
                    children=None,
                    attrs={"id": "template id"},
                ),
            ],
        )

    @patch("sys.stdout", new_callable=StringIO)
    @patch("checkbox_ng.launcher.subcommands.Explorer")
    def test_print_objs_nojson(self, mock_explorer, stdout_mock):
        mock_explorer().get_object_tree.return_value = self.get_test_tree()

        print_objs(group="job", sa=MagicMock(), show_attrs=True)
        printed = stdout_mock.getvalue()
        self.assertIn("job name", printed)
        self.assertIn("job id", printed)
        self.assertNotIn("exporter id", printed)
        self.assertNotIn("exporter id", printed)

    @patch("sys.stdout", new_callable=StringIO)
    @patch("checkbox_ng.launcher.subcommands.Explorer")
    def test_print_objs_json(self, mock_explorer, stdout_mock):
        mock_explorer().get_object_tree.return_value = self.get_test_tree()
        print_objs(
            group="job", sa=MagicMock(), show_attrs=True, json_repr=True
        )
        printed = stdout_mock.getvalue()
        self.assertIn("job name", printed)
        self.assertIn("job id", printed)
        self.assertNotIn("exporter id", printed)
        self.assertNotIn("exporter id", printed)

    @patch("sys.stdout", new_callable=StringIO)
    @patch("checkbox_ng.launcher.subcommands.Explorer")
    def test_print_objs_json_print_all(self, mock_explorer, stdout_mock):
        mock_explorer().get_object_tree.return_value = self.get_test_tree()
        print_objs(
            group=None, sa=MagicMock(), show_attrs=False, json_repr=True
        )
        printed = stdout_mock.getvalue()
        self.assertIn("job name", printed)
        self.assertNotIn(
            "job id", printed
        )  # job id is an attr, so shouldnt be here
        self.assertIn("exporter name", printed)
        self.assertNotIn("exporter id", printed)  # same for exporter id

    @patch("sys.stdout", new_callable=StringIO)
    @patch("checkbox_ng.launcher.subcommands.Explorer")
    def test_print_objs_json_print_all_jobs(self, mock_explorer, stdout_mock):
        mock_explorer().get_object_tree.return_value = self.get_test_tree()
        print_objs(
            group="all-jobs", sa=MagicMock(), show_attrs=False, json_repr=True
        )
        printed = stdout_mock.getvalue()
        self.assertIn("job name", printed)
        self.assertIn("template name", printed)
        self.assertNotIn("exporter name", printed)


class TestLauncher(TestCase):
    @patch(
        "checkbox_ng.launcher.subcommands.open",
        new_callable=mock_open,
        read_data=textwrap.dedent(
            """
            [launcher]
            app_id = "appid"
            app_version = 0
            session_title = "session_title"
            session_desc = "description"
            """
        ),
    )
    def test_start_new_session_ok(self, _):
        self_mock = MagicMock()
        self_mock.is_interactive = True
        self_mock._interactively_pick_test_plan.return_value = "test plan id"
        self_mock.ctx.args.launcher = "launcher_path.conf"
        self_mock.ctx.args.message = None

        def configuration_get_value(tl_key, sl_key):
            configuration = {
                "launcher": {
                    "app_id": "appid",
                    "app_version": 0,
                    "session_title": "session_title",
                    "session_desc": "description",
                },
                "agent": {"normal_user": "ubuntu"},
                "test plan": {"forced": False, "unit": "some unit"},
            }
            return configuration[tl_key][sl_key]

        self_mock.configuration.get_value = configuration_get_value

        Launcher._start_new_session(self_mock)

    @patch("checkbox_ng.launcher.subcommands.open")
    def test_start_new_session_ok_no_launcher(self, mock_open):
        self_mock = MagicMock()
        self_mock.is_interactive = True
        self_mock._interactively_pick_test_plan.return_value = "test plan id"
        self_mock.ctx.args.launcher = "launcher_path.conf"
        self_mock.ctx.args.message = None
        mock_open.side_effect = FileNotFoundError

        def configuration_get_value(tl_key, sl_key):
            configuration = {
                "launcher": {
                    "app_id": "appid",
                    "app_version": 0,
                    "session_title": "session_title",
                    "session_desc": "description",
                },
                "agent": {"normal_user": "ubuntu"},
                "test plan": {"forced": False, "unit": "some unit"},
            }
            return configuration[tl_key][sl_key]

        self_mock.configuration.get_value = configuration_get_value

        Launcher._start_new_session(self_mock)

    @patch("checkbox_ng.launcher.subcommands.detect_restart_strategy")
    @patch("os.getenv")
    @patch("sys.argv")
    @patch("os.path.abspath")
    def test__configure_restart(
        self, abspath_mock, sys_argv_mock, mock_getenv, mock_rs
    ):
        tested_self = Mock()
        ctx_mock = Mock()
        mock_getenv.return_value = ""
        sys_argv_mock.__getitem__.return_value = "unittest"
        abspath_mock.return_value = "launcher_path"

        Launcher._configure_restart(tested_self, ctx_mock)
        (get_restart_cmd_f,) = (
            ctx_mock.sa.configure_application_restart.call_args[0]
        )
        restart_cmd = get_restart_cmd_f("session_id")
        self.assertEqual(
            restart_cmd,
            ["unittest launcher launcher_path --resume session_id"],
        )

    @patch("checkbox_ng.launcher.subcommands.detect_restart_strategy")
    @patch("os.getenv")
    @patch("sys.argv")
    @patch("os.path.abspath")
    def test__configure_restart_snap(
        self, abspath_mock, sys_argv_mock, mock_getenv, mock_rs
    ):
        tested_self = Mock()
        ctx_mock = Mock()
        mock_getenv.return_value = "snap_name"
        sys_argv_mock.__getitem__.return_value = "unittest"
        abspath_mock.return_value = "launcher_path"

        Launcher._configure_restart(tested_self, ctx_mock)
        (get_restart_cmd_f,) = (
            ctx_mock.sa.configure_application_restart.call_args[0]
        )
        restart_cmd = get_restart_cmd_f("session_id")
        self.assertEqual(
            restart_cmd,
            [
                "/snap/bin/snap_name.checkbox-cli launcher "
                "launcher_path --resume session_id"
            ],
        )

    @patch("checkbox_ng.launcher.subcommands.ResumeMenu")
    def test__manually_resume_session_delete(self, resume_menu_mock):
        self_mock = MagicMock()
        resume_menu_mock().run().action = "delete"

        # delete something, the check should see that the entries list is
        # empty and return false as there is nothing to maybe resume
        self.assertFalse(Launcher._manually_resume_session(self_mock, []))

    @patch("checkbox_ng.launcher.subcommands.ResumeMenu")
    def test__manually_resume_session(self, resume_menu_mock):
        self_mock = MagicMock()
        resume_menu_mock().run().session_id = "nonempty"

        # the user has selected something from the list, we notice
        self.assertTrue(Launcher._manually_resume_session(self_mock, []))
        # and we try to resume the session
        self.assertTrue(self_mock._resume_session_via_resume_params.called)

    @patch("checkbox_ng.launcher.subcommands.ResumeMenu")
    def test__manually_resume_session_empty_id(self, resume_menu_mock):
        self_mock = MagicMock()
        resume_menu_mock().run().session_id = ""

        self.assertFalse(Launcher._manually_resume_session(self_mock, []))

    @patch("checkbox_ng.launcher.subcommands.Configuration")
    @patch("checkbox_ng.launcher.subcommands.load_configs")
    def test_load_configs_from_app_blob(
        self, load_config_mock, configuration_mock
    ):
        self_mock = MagicMock()
        app_blob = {
            "launcher": textwrap.dedent(
                """
                [launcher]
                launcher_version = 1
                """
            )
        }

        Launcher.load_configs_from_app_blob(self_mock, app_blob)

        self.assertTrue(configuration_mock.from_text.called)
        self.assertTrue(load_config_mock.called)
        self.assertTrue(self_mock.ctx.sa.use_alternate_configuration.called)

    @patch("checkbox_ng.launcher.subcommands.Configuration")
    @patch("checkbox_ng.launcher.subcommands.load_configs")
    def test_load_configs_from_app_blob_no_launcher(
        self, load_config_mock, configuration_mock
    ):
        self_mock = MagicMock()
        app_blob = {}

        Launcher.load_configs_from_app_blob(self_mock, app_blob)

        self.assertFalse(configuration_mock.from_text.called)
        self.assertTrue(load_config_mock.called)
        self.assertTrue(self_mock.ctx.sa.use_alternate_configuration.called)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_pass(self, memory_job_result_mock):
        self_mock = MagicMock()
        self_mock._resume_session = partial(
            Launcher._resume_session, self_mock
        )
        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = ["testplanless"]

        resume_params_mock = MagicMock()
        resume_params_mock.action = "pass"

        Launcher._resume_session_via_resume_params(
            self_mock, resume_params_mock
        )

        args, _ = memory_job_result_mock.call_args_list[-1]
        result_dict, *_ = args
        self.assertEqual(result_dict["outcome"], IJobResult.OUTCOME_PASS)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.request_comment")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_fail_cert_blocker(
        self, request_comment_mock, memory_job_result_mock
    ):
        self_mock = MagicMock()
        self_mock._resume_session = partial(
            Launcher._resume_session, self_mock
        )
        self_mock.ctx.sa.get_job_state.return_value.effective_certification_status = (
            "blocker"
        )

        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = ["testplanless"]

        resume_params_mock = MagicMock()
        resume_params_mock.action = "fail"
        resume_params_mock.comments = None

        Launcher._resume_session_via_resume_params(
            self_mock, resume_params_mock
        )

        args, _ = memory_job_result_mock.call_args_list[-1]
        result_dict, *_ = args
        self.assertEqual(result_dict["outcome"], IJobResult.OUTCOME_FAIL)
        # given that no comment was in resume_params, the resume procedure asks for it
        self.assertTrue(request_comment_mock.called)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_fail_non_blocker(self, memory_job_result_mock):
        self_mock = MagicMock()
        self_mock._resume_session = partial(
            Launcher._resume_session, self_mock
        )
        self_mock.ctx.sa.get_job_state.return_value.effective_certification_status = (
            "non-blocker"
        )

        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = ["testplanless"]

        resume_params_mock = MagicMock()
        resume_params_mock.action = "fail"

        Launcher._resume_session_via_resume_params(
            self_mock, resume_params_mock
        )

        args, _ = memory_job_result_mock.call_args_list[-1]
        result_dict, *_ = args
        self.assertEqual(result_dict["outcome"], IJobResult.OUTCOME_FAIL)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.request_comment")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_crash_cert_blocker(
        self, request_comment_mock, memory_job_result_mock
    ):
        self_mock = MagicMock()
        self_mock.ctx.sa.get_job_state.return_value.effective_certification_status = (
            "blocker"
        )

        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = ["testplanless"]

        Launcher._resume_session(
            self_mock, "session_id", IJobResult.OUTCOME_CRASH, None
        )

        args, _ = memory_job_result_mock.call_args_list[-1]
        result_dict, *_ = args
        self.assertEqual(result_dict["outcome"], IJobResult.OUTCOME_CRASH)
        # given that no comment was in resume_params, the resume procedure asks for it
        self.assertTrue(request_comment_mock.called)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_crash_non_blocker(self, memory_job_result_mock):
        self_mock = MagicMock()
        self_mock.ctx.sa.get_job_state.return_value.effective_certification_status = (
            "non-blocker"
        )

        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = ["testplanless"]

        Launcher._resume_session(
            self_mock, "session_id", IJobResult.OUTCOME_CRASH, None
        )

        args, _ = memory_job_result_mock.call_args_list[-1]
        result_dict, *_ = args
        self.assertEqual(result_dict["outcome"], IJobResult.OUTCOME_CRASH)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.request_comment")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_skip_blocker(
        self, request_comment_mock, memory_job_result_mock
    ):
        self_mock = MagicMock()
        self_mock._resume_session = partial(
            Launcher._resume_session, self_mock
        )
        self_mock.ctx.sa.get_job_state.return_value.effective_certification_status = (
            "blocker"
        )

        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = ["testplanless"]

        resume_params_mock = MagicMock()
        resume_params_mock.action = "skip"
        resume_params_mock.comments = None

        Launcher._resume_session_via_resume_params(
            self_mock, resume_params_mock
        )

        args, _ = memory_job_result_mock.call_args_list[-1]
        result_dict, *_ = args
        self.assertEqual(result_dict["outcome"], IJobResult.OUTCOME_SKIP)
        # given that no comment was in resume_params, the resume procedure asks for it
        self.assertTrue(request_comment_mock.called)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_skip_non_blocker(self, memory_job_result_mock):
        self_mock = MagicMock()
        self_mock._resume_session = partial(
            Launcher._resume_session, self_mock
        )
        self_mock.ctx.sa.get_job_state.return_value.effective_certification_status = (
            "non-blocker"
        )

        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = ["testplanless"]

        resume_params_mock = MagicMock()
        resume_params_mock.action = "skip"

        Launcher._resume_session_via_resume_params(
            self_mock, resume_params_mock
        )

        args, _ = memory_job_result_mock.call_args_list[-1]
        result_dict, *_ = args
        self.assertEqual(result_dict["outcome"], IJobResult.OUTCOME_SKIP)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_rerun(self, memory_job_result_mock):
        self_mock = MagicMock()
        self_mock._resume_session = partial(
            Launcher._resume_session, self_mock
        )
        self_mock.ctx.sa.get_job_state.return_value.effective_certification_status = (
            "non-blocker"
        )

        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = ["testplanless"]

        resume_params_mock = MagicMock()
        resume_params_mock.action = "rerun"

        Launcher._resume_session_via_resume_params(
            self_mock, resume_params_mock
        )

        # we don't use job result of rerun jobs
        self.assertFalse(self_mock.ctx.sa.use_job_result.called)

    @patch("checkbox_ng.launcher.subcommands.MemoryJobResult")
    @patch("checkbox_ng.launcher.subcommands.newline_join", new=MagicMock())
    def test__resume_session_autocalculate_outcome(
        self, memory_job_result_mock
    ):
        self_mock = MagicMock()
        self_mock.ctx.sa.get_job_state.return_value.effective_certification_status = (
            "non-blocker"
        )
        self_mock._get_autoresume_outcome_last_job.return_value = (
            IJobResult.OUTCOME_CRASH
        )

        session_metadata_mock = self_mock.ctx.sa.resume_session.return_value
        session_metadata_mock.flags = []
        session_metadata_mock.app_blob = b'{"testplan_id" : "testplan_id"}'

        Launcher._resume_session(self_mock, "session_id", None, None)

        args, _ = memory_job_result_mock.call_args_list[-1]
        result_dict, *_ = args
        self.assertEqual(result_dict["outcome"], IJobResult.OUTCOME_CRASH)

    def test__get_autoresume_outcome_last_job_noreturn(self):
        self_mock = MagicMock()
        job_state = self_mock.sa.get_job_state()
        job_state.job.flags = "noreturn"
        job_state.result.outcome = None
        job_state.result.comments = None

        metadata_mock = MagicMock()
        metadata_mock.running_job_name = "running_metadata_job_name"

        outcome = Launcher._get_autoresume_outcome_last_job(
            self_mock, metadata_mock
        )

        self.assertEqual(outcome, IJobResult.OUTCOME_PASS)

    def test__get_autoresume_outcome_last_job_crashed(self):
        self_mock = MagicMock()
        job_state = self_mock.sa.get_job_state()
        job_state.job.flags = ""
        job_state.result.outcome = None
        job_state.result.comments = None

        metadata_mock = MagicMock()
        metadata_mock.running_job_name = "running_metadata_job_name"

        outcome = Launcher._get_autoresume_outcome_last_job(
            self_mock, metadata_mock
        )

        self.assertEqual(outcome, IJobResult.OUTCOME_CRASH)

    def test__get_autoresume_outcome_last_job_already_set(self):
        self_mock = MagicMock()
        job_state = self_mock.sa.get_job_state()
        job_state.job.flags = ""
        job_state.result.outcome = IJobResult.OUTCOME_PASS
        job_state.result.comments = "Pre resume comment"

        metadata_mock = MagicMock()
        metadata_mock.running_job_name = "running_metadata_job_name"

        outcome = Launcher._get_autoresume_outcome_last_job(
            self_mock, metadata_mock
        )

        self.assertEqual(outcome, IJobResult.OUTCOME_PASS)

    def test__resumed_session(self):
        self_mock = MagicMock()

        with Launcher._resumed_session(self_mock, "session_id"):
            self.assertTrue(self_mock.sa.resume_session.called)
            self.assertFalse(self_mock.ctx.reset_sa.called)
        self.assertTrue(self_mock.ctx.reset_sa.called)

    def test__should_autoresume_last_run_no_candidate(self):
        self_mock = MagicMock()

        self.assertFalse(Launcher._should_autoresume_last_run(self_mock, []))

    @patch("os.getenv", return_value="checkbox22")
    @patch("checkbox_ng.launcher.subcommands.input")
    @patch("checkbox_ng.launcher.subcommands._logger")
    def test__should_autoresume_last_run_incompatible_session_snaps(
        self, _logger_mock, input_mock, os_getenv_mock
    ):
        self_mock = MagicMock()
        self_mock._resumed_session = partial(
            Launcher._resumed_session, self_mock
        )
        session_mock = MagicMock(id="session_id")

        self_mock.sa.resume_session.side_effect = IncompatibleJobError

        self.assertFalse(
            Launcher._should_autoresume_last_run(self_mock, [session_mock])
        )
        # very important here that we print errors and stop because else the
        # user is left wondering why the session didn't autoresume
        self.assertTrue(_logger_mock.error.called)
        self.assertTrue(input_mock.called)

    @patch("os.getenv", return_value=None)
    @patch("checkbox_ng.launcher.subcommands.input")
    @patch("checkbox_ng.launcher.subcommands._logger")
    def test__should_autoresume_last_run_incompatible_session_debs(
        self, _logger_mock, input_mock, os_getenv_mock
    ):
        self_mock = MagicMock()
        self_mock._resumed_session = partial(
            Launcher._resumed_session, self_mock
        )
        session_mock = MagicMock(id="session_id")

        self_mock.sa.resume_session.side_effect = IncompatibleJobError

        self.assertFalse(
            Launcher._should_autoresume_last_run(self_mock, [session_mock])
        )
        # very important here that we print errors and stop because else the
        # user is left wondering why the session didn't autoresume
        self.assertTrue(_logger_mock.error.called)
        self.assertTrue(input_mock.called)

    def test__should_autoresume_last_run_no_testplan(self):
        self_mock = MagicMock()
        self_mock._resumed_session = partial(
            Launcher._resumed_session, self_mock
        )
        session_mock = MagicMock(id="session_id")
        metadata_mock = MagicMock(app_blob=b"{}")
        self_mock.sa.resume_session.return_value = metadata_mock

        self.assertFalse(
            Launcher._should_autoresume_last_run(self_mock, [session_mock])
        )

    def test__should_autoresume_last_run_no_running_job_name(self):
        self_mock = MagicMock()
        self_mock._resumed_session = partial(
            Launcher._resumed_session, self_mock
        )
        session_mock = MagicMock(id="session_id")
        metadata_mock = MagicMock(
            app_blob=b'{"testplan_id" : "testplan_id"}', running_job_name=None
        )
        self_mock.sa.resume_session.return_value = metadata_mock

        self.assertFalse(
            Launcher._should_autoresume_last_run(self_mock, [session_mock])
        )

    def test__should_autoresume_last_run_manual_job(self):
        self_mock = MagicMock()
        self_mock._resumed_session = partial(
            Launcher._resumed_session, self_mock
        )
        session_mock = MagicMock(id="session_id")
        metadata_mock = MagicMock(
            app_blob=b'{"testplan_id" : "testplan_id"}',
            running_job_name="running_job_name",
        )
        self_mock.sa.resume_session.return_value = metadata_mock
        job_state_mock = self_mock.sa.get_job_state()
        job_state_mock.job.plugin = "user-interact"

        self.assertFalse(
            Launcher._should_autoresume_last_run(self_mock, [session_mock])
        )

    def test__should_autoresume_last_run_yes(self):
        self_mock = MagicMock()
        self_mock._resumed_session = partial(
            Launcher._resumed_session, self_mock
        )
        session_mock = MagicMock(id="session_id")
        metadata_mock = MagicMock(
            app_blob=b'{"testplan_id" : "testplan_id"}',
            running_job_name="running_job_name",
        )
        self_mock.sa.resume_session.return_value = metadata_mock
        job_state_mock = self_mock.sa.get_job_state()
        job_state_mock.job.plugin = "shell"

        self.assertTrue(
            Launcher._should_autoresume_last_run(self_mock, [session_mock])
        )

    def test__auto_resume_session_from_ctx(self):
        self_mock = MagicMock()
        resume_candidate_mock = MagicMock(id="session_to_resume")
        self_mock.ctx.args.session_id = "session_to_resume"

        self.assertTrue(
            Launcher._auto_resume_session(self_mock, [resume_candidate_mock])
        )
        self.assertTrue(self_mock._resume_session.called)

    def test__auto_resume_session_from_ctx_unknown_session(self):
        self_mock = MagicMock()
        resume_candidate_mock = MagicMock(id="some_other_session")
        self_mock.ctx.args.session_id = "session_to_resume"

        with self.assertRaises(RuntimeError):
            self.assertTrue(
                Launcher._auto_resume_session(
                    self_mock, [resume_candidate_mock]
                )
            )

    def test__auto_resume_session_autoresume(self):
        self_mock = MagicMock()
        resume_candidate_mock = MagicMock(id="session_to_resume")
        # session id wasn't provided directly via the cli
        self_mock.ctx.args.session_id = None
        # --clear-old-sessions wasn't used
        self_mock.ctx.args.clear_old_sessions = False
        self_mock._should_autoresume_last_run.return_value = True

        self.assertTrue(
            Launcher._auto_resume_session(self_mock, [resume_candidate_mock])
        )
        self.assertTrue(self_mock._resume_session.called)

    def test__auto_resume_session_no_autoresume_on_clear(self):
        self_mock = MagicMock()
        resume_candidate_mock = MagicMock(id="session_to_resume")
        # session id wasn't provided directly via the cli
        self_mock.ctx.args.session_id = None
        # --clear-old-sessions was used, so we don't autoresume
        self_mock.ctx.args.clear_old_sessions = True

        self.assertFalse(
            Launcher._auto_resume_session(self_mock, [resume_candidate_mock])
        )
        self.assertFalse(self_mock._resume_session.called)

    def test__auto_resume_session_no_autoresume(self):
        self_mock = MagicMock()
        resume_candidate_mock = MagicMock(id="session_to_resume")
        self_mock.ctx.args.session_id = None
        self_mock._should_autoresume_last_run.return_value = False

        self.assertFalse(
            Launcher._auto_resume_session(self_mock, [resume_candidate_mock])
        )
        self.assertFalse(self_mock._resume_session.called)

    @patch("checkbox_ng.launcher.subcommands.load_configs")
    @patch("checkbox_ng.launcher.subcommands.Colorizer", new=MagicMock())
    def test_invoked_resume(self, load_config_mock):
        self_mock = MagicMock()
        self_mock._maybe_auto_resume_session.side_effect = [False, True]
        self_mock._pick_jobs_to_run.side_effect = ResumeInstead()

        ctx_mock = MagicMock()
        ctx_mock.args.verify = False
        ctx_mock.args.version = False
        ctx_mock.args.verbose = False
        ctx_mock.args.debug = False
        ctx_mock.sa.get_resumable_sessions.return_value = []
        ctx_mock.sa.get_static_todo_list.return_value = False

        load_config_mock.return_value.get_value.return_value = "normal"

        Launcher.invoked(self_mock, ctx_mock)

    def test__save_manifest_no_or_empty_manifest_repr(self):
        launcher = Launcher()
        ctx_mock = MagicMock()
        launcher.ctx = ctx_mock

        cases = [
            ("None", None),
            ("Empty", {}),
        ]

        for case_name, manifest_repr in cases:
            with self.subTest(case=case_name):
                ctx_mock.sa.get_manifest_repr.return_value = manifest_repr
                launcher._save_manifest(interactive=True)
                self.assertEqual(ctx_mock.sa.save_manifest.call_count, 0)

    @patch("checkbox_ng.launcher.subcommands.ManifestBrowser")
    def test__save_manifest_interactive_with_visible_manifests(
        self, mock_browser_class
    ):

        launcher = Launcher()
        ctx_mock = MagicMock()
        launcher.ctx = ctx_mock

        manifest_repr = {
            "section1": [
                {"id": "visible1", "value": 0, "hidden": False},
                {"id": "visible2", "value": False, "hidden": False},
            ]
        }
        ctx_mock.sa.get_manifest_repr.return_value = manifest_repr

        mock_browser = MagicMock()
        mock_browser.run.return_value = {
            "visible1": 5,
            "visible2": True,
        }
        mock_browser_class.return_value = mock_browser
        mock_browser_class.has_visible_manifests.return_value = True

        launcher._save_manifest(interactive=True)

        ctx_mock.sa.save_manifest.assert_called_with(
            {"visible1": 5, "visible2": True}
        )

    @patch("checkbox_ng.launcher.subcommands.ManifestBrowser")
    def test__save_manifest_interactive_no_visible_manifests(
        self, mock_browser_class
    ):
        launcher = Launcher()
        ctx_mock = MagicMock()
        launcher.ctx = ctx_mock

        manifest_repr = {
            "section1": [
                {"id": "hidden1", "value": True, "hidden": True},
                {"id": "hidden2", "value": 2, "hidden": True},
            ]
        }
        ctx_mock.sa.get_manifest_repr.return_value = manifest_repr
        mock_browser_class.has_visible_manifests.return_value = False
        mock_browser_class.get_flattened_values.return_value = {
            "hidden1": True,
            "hidden2": 2,
        }

        launcher._save_manifest(interactive=True)

        ctx_mock.sa.save_manifest.assert_called_with(
            {"hidden1": True, "hidden2": 2}
        )

    @patch("checkbox_ng.launcher.subcommands.ManifestBrowser")
    def test__save_manifest_non_interactive(self, mock_browser_class):
        launcher = Launcher()
        ctx_mock = MagicMock()
        launcher.ctx = ctx_mock

        manifest_repr = {
            "section1": [
                {"id": "manifest1", "value": False, "hidden": False},
                {"id": "manifest2", "value": 7, "hidden": True},
            ]
        }
        ctx_mock.sa.get_manifest_repr.return_value = manifest_repr
        mock_browser_class.get_flattened_values.return_value = {
            "manifest1": False,
            "manifest2": 7,
        }

        launcher._save_manifest(interactive=False)

        ctx_mock.sa.save_manifest.assert_called_with(
            {"manifest1": False, "manifest2": 7}
        )


@patch("os.makedirs", new=MagicMock())
class TestLauncherReturnCodes(TestCase):
    def setUp(self):
        self.launcher = Launcher()
        self.launcher._maybe_rerun_jobs = Mock(return_value=False)
        self.launcher._auto_resume_session = Mock(return_value=False)
        self.launcher._resume_session_via_resume_params = Mock(
            return_value=False
        )
        self.launcher._start_new_session = Mock()
        self.launcher._pick_jobs_to_run = Mock()
        self.launcher._export_results = Mock()
        self.ctx = Mock()
        self.ctx.args = Mock(version=False, verify=False, launcher="")
        self.ctx.sa = Mock(
            get_resumable_sessions=Mock(return_value=[]),
            get_dynamic_todo_list=Mock(return_value=[]),
        )

    def test_invoke_returns_0_on_no_fails(self):
        mock_results = {"fail": 0, "crash": 0, "pass": 1}
        self.ctx.sa.get_summary = Mock(return_value=mock_results)
        self.assertEqual(self.launcher.invoked(self.ctx), 0)

    def test_invoke_returns_1_on_fail(self):
        mock_results = {"fail": 1, "crash": 0, "pass": 1}
        self.ctx.sa.get_summary = Mock(return_value=mock_results)
        self.assertEqual(self.launcher.invoked(self.ctx), 1)

    def test_invoke_returns_1_on_crash(self):
        mock_results = {"fail": 0, "crash": 1, "pass": 1}
        self.ctx.sa.get_summary = Mock(return_value=mock_results)
        self.assertEqual(self.launcher.invoked(self.ctx), 1)

    def test_invoke_returns_0_on_no_tests(self):
        mock_results = {"fail": 0, "crash": 0, "pass": 0}
        self.ctx.sa.get_summary = Mock(return_value=mock_results)
        self.assertEqual(self.launcher.invoked(self.ctx), 0)

    def test_invoke_returns_1_on_many_diff_outcomes(self):
        mock_results = {"fail": 6, "crash": 7, "pass": 8}
        self.ctx.sa.get_summary = Mock(return_value=mock_results)
        self.assertEqual(self.launcher.invoked(self.ctx), 1)


class TestLListBootstrapped(TestCase):
    def setUp(self):
        self.launcher = ListBootstrapped()
        self.ctx = Mock()
        self.ctx.args = Mock(TEST_PLAN="", format="")
        self.ctx.sa = Mock(
            start_new_session=Mock(),
            get_test_plans=Mock(return_value=["test-plan1", "test-plan2"]),
            select_test_plan=Mock(),
            bootstrap=Mock(),
            get_static_todo_list=Mock(return_value=["test-job1", "test-job2"]),
            get_job=Mock(
                side_effect=[
                    Mock(
                        _raw_data={
                            "id": "namespace1::test-job1",
                            "summary": "fake-job1",
                            "plugin": "manual",
                            "description": "fake-description1",
                            "certification_status": "non-blocker",
                        },
                        id="namespace1::test-job1",
                        partial_id="test-job1",
                    ),
                    Mock(
                        _raw_data={
                            "id": "namespace2::test-job2",
                            "summary": "fake-job2",
                            "plugin": "shell",
                            "command": "ls",
                            "certification_status": "non-blocker",
                        },
                        id="namespace2::test-job2",
                        partial_id="test-job2",
                    ),
                ]
            ),
            get_job_state=Mock(
                return_value=Mock(effective_certification_status="blocker")
            ),
            get_resumable_sessions=Mock(return_value=[]),
            get_dynamic_todo_list=Mock(return_value=[]),
        )

    def test_invoke_test_plan_not_found(self):
        self.ctx.args.TEST_PLAN = "test-plan3"

        with self.assertRaisesRegex(SystemExit, "Test plan not found"):
            self.launcher.invoked(self.ctx)

    @patch("sys.stdout", new_callable=StringIO)
    def test_invoke_print_output_format(self, stdout):
        self.ctx.args.TEST_PLAN = "test-plan1"
        self.ctx.args.format = "?"

        expected_out = (
            "Available fields are:\ncertification_status, command, "
            "description, full_id, id, plugin, summary\n"
        )
        self.launcher.invoked(self.ctx)
        self.assertEqual(stdout.getvalue(), expected_out)

    @patch("sys.stdout", new_callable=StringIO)
    def test_invoke_print_output_standard_format(self, stdout):
        self.ctx.args.TEST_PLAN = "test-plan1"
        self.ctx.args.format = "{full_id}\n"

        expected_out = "namespace1::test-job1\n" "namespace2::test-job2\n"
        self.launcher.invoked(self.ctx)
        self.assertEqual(stdout.getvalue(), expected_out)

    @patch("sys.stdout", new_callable=StringIO)
    def test_invoke_print_output_customized_format(self, stdout):
        self.ctx.args.TEST_PLAN = "test-plan1"
        self.ctx.args.format = (
            "id: {id}\nplugin: {plugin}\nsummary: {summary}\n"
            "certification blocker: {certification_status}\n\n"
        )

        expected_out = (
            "id: test-job1\n"
            "plugin: manual\n"
            "summary: fake-job1\n"
            "certification blocker: blocker\n\n"
            "id: test-job2\n"
            "plugin: shell\n"
            "summary: fake-job2\n"
            "certification blocker: blocker\n\n"
        )
        self.launcher.invoked(self.ctx)
        self.assertEqual(stdout.getvalue(), expected_out)


class TestExpand(TestCase):
    def make_unit(self, **kwargs):
        unit = Mock(partial_id=kwargs["id"], **kwargs)
        unit._raw_data.copy.return_value = kwargs
        return unit

    def setUp(self):
        self.launcher = Expand()
        self.ctx = Mock()
        self.ctx.args = Mock(TEST_PLAN="", format="")

        selected_1 = self.make_unit(
            unit="manifest entry",
            id="some",
            is_hidden=False,
        )
        selected_2 = self.make_unit(
            unit="manifest entry",
            id="other",
            is_hidden=False,
        )
        not_selected = self.make_unit(unit="manifest entry", id="not_selected")
        # hidden manifests are not hidden in the expose output
        hidden = self.make_unit(
            unit="manifest entry", id="_hidden", is_hidden=True
        )

        self.ctx.sa = Mock(
            start_new_session=Mock(),
            get_test_plans=Mock(return_value=["test-plan1", "test-plan2"]),
            select_test_plan=Mock(),
            # get_resumable_sessions=Mock(return_value=[]),
            _context=Mock(
                state=Mock(unit_list=[]),
                _test_plan_list=[Mock()],
                unit_list=[
                    selected_1,
                    selected_2,
                    not_selected,
                    hidden,
                ],
            ),
        )

    def test_register_arguments(self):
        parser_mock = Mock()
        self.launcher.register_arguments(parser_mock)
        self.assertTrue(parser_mock.add_argument.called)

    def test_invoke__test_plan_not_found(self):
        self.ctx.args.TEST_PLAN = "test-plan3"

        with self.assertRaisesRegex(SystemExit, "Test plan not found"):
            self.launcher.invoked(self.ctx)

    @patch("sys.stdout", new_callable=StringIO)
    @patch("checkbox_ng.launcher.subcommands.TestPlanUnitSupport")
    @patch("checkbox_ng.launcher.subcommands.select_units")
    def test_invoke__text(self, mock_select_units, mock_tpus, stdout):
        template1 = TemplateUnit(
            {
                "template-id": "test-template",
                "id": "test-{res}",
                "template-summary": "Test Template Summary",
                "requires": "manifest.some == 'True'\nmanifest._hidden == 'False'",
            }
        )
        job1 = JobDefinition(
            {
                "id": "job1",
                "requires": "manifest.other == 'Other'",
            }
        )
        mock_select_units.return_value = [job1, template1]
        self.ctx.args.TEST_PLAN = "test-plan1"
        self.launcher.invoked(self.ctx)
        self.assertIn("Template 'test-template'", stdout.getvalue())
        self.assertIn("Manifest 'some'", stdout.getvalue())
        self.assertIn("Manifest 'other'", stdout.getvalue())
        self.assertIn("Manifest '_hidden'", stdout.getvalue())
        self.assertNotIn("Manifest 'not_selected'", stdout.getvalue())

    @patch("sys.stdout", new_callable=StringIO)
    @patch("checkbox_ng.launcher.subcommands.TestPlanUnitSupport")
    @patch("checkbox_ng.launcher.subcommands.select_units")
    def test_invoke__json(self, mock_select_units, mock_tpus, stdout):
        template1 = TemplateUnit(
            {
                "template-id": "test-template",
                "id": "test-{res}",
                "template-summary": "Test Template Summary",
                "requires": "manifest.some == 'True'\nmanifest._hidden == 'False'",
            }
        )
        job1 = JobDefinition(
            {
                "id": "job1",
                "requires": "manifest.other == 'Other'",
            }
        )

        mock_select_units.return_value = [job1, template1]
        self.ctx.args.TEST_PLAN = "test-plan1"
        self.ctx.args.format = "json"
        self.launcher.invoked(self.ctx)
        self.assertIn('"template-id": "test-template"', stdout.getvalue())
        self.assertIn('"id": "some"', stdout.getvalue())
        self.assertIn('"id": "other"', stdout.getvalue())
        self.assertIn('"id": "_hidden"', stdout.getvalue())
        self.assertNotIn('"id": "not_selected"', stdout.getvalue())

    def test_get_effective_certificate_status(self):
        job1 = JobDefinition(
            {
                "id": "job1",
            }
        )
        template1 = TemplateUnit(
            {
                "template-id": "template1",
                "id": "job-{res}",
            }
        )
        self.launcher.override_list = [
            (
                "^job1$",
                [
                    ("certification_status", "blocker"),
                ],
            ),
        ]
        self.assertEqual(
            self.launcher.get_effective_certification_status(job1), "blocker"
        )
        self.assertEqual(
            self.launcher.get_effective_certification_status(template1),
            "non-blocker",
        )


class TestUtilsFunctions(TestCase):
    @patch("checkbox_ng.launcher.subcommands.Colorizer", new=MagicMock())
    @patch("builtins.print")
    @patch("builtins.input")
    def test_request_comment(self, input_mock, print_mock):
        input_mock.side_effect = ["", "failure"]

        comment = request_comment("Job Name")

        self.assertEqual(comment, "failure")

    def test_generate_resume_candidate_description_default_time(self):
        candidate_mock = MagicMock()
        candidate_mock.metadata.app_blob = b'{ "testplan_id" : "123" }'
        candidate_mock.metadata.title = "Title"
        candidate_mock.metadata.last_job_start_time = None
        candidate_mock.metadata.running_job_name = "Test"

        description = generate_resume_candidate_description(candidate_mock)

        self.assertIn("Unknown", description)
        self.assertIn("123", description)
        self.assertIn("Title", description)
        self.assertIn("Test", description)

    def test_generate_resume_candidate_description(self):
        candidate_mock = MagicMock()
        candidate_mock.metadata.app_blob = b'{ "testplan_id" : "123" }'
        candidate_mock.metadata.title = "Title"
        candidate_mock.metadata.last_job_start_time = 1
        # let's create a real point in time that we can verify on the screen
        date = datetime.datetime(2023, 1, 1, tzinfo=datetime.timezone.utc)
        candidate_mock.metadata.last_job_start_time = date.timestamp()
        candidate_mock.metadata.running_job_name = "Test"

        description = generate_resume_candidate_description(candidate_mock)

        self.assertIn("2023", description)
        self.assertIn("123", description)
        self.assertIn("Title", description)
        self.assertIn("Test", description)

    @patch("checkbox_ng.launcher.subcommands.Explorer")
    def test_multiple_relevant_found_raises_system_exit(
        self, mock_explorer_class
    ):
        mock_unit1 = MagicMock()
        mock_unit1.name = "namespace1::some"
        mock_unit2 = MagicMock()
        mock_unit2.name = "namespace2::some"

        mock_root = MagicMock()
        mock_root.find_children_by_name.return_value = {
            "key": [mock_unit1, mock_unit2]
        }
        mock_explorer_instance = mock_explorer_class()
        mock_explorer_instance.get_object_tree.return_value = mock_root

        with self.assertRaises(SystemExit):
            get_testplan_id_by_id(
                ["namespace1::some", "namespace2::some"],
                "some",
                MagicMock(),
                exact=False,
            )

    @patch("checkbox_ng.launcher.subcommands.Explorer")
    def test_single_relevant_found_returns_name(self, mock_explorer_class):
        mock_unit = MagicMock()
        mock_unit.name = "namespace1::some"

        mock_root = MagicMock()
        mock_root.find_children_by_name.return_value = {"some": [mock_unit]}
        mock_explorer_instance = mock_explorer_class()
        mock_explorer_instance.get_object_tree.return_value = mock_root

        result = get_testplan_id_by_id(
            ["namespace1::some"], "some", MagicMock(), exact=False
        )
        self.assertEqual(result, "namespace1::some")

    @patch("checkbox_ng.launcher.subcommands.Explorer")
    def test_no_relevant_found_returns_original_id(self, mock_explorer_class):
        mock_root = MagicMock()
        mock_root.find_children_by_name.return_value = {}
        mock_explorer_instance = mock_explorer_class()
        mock_explorer_instance.get_object_tree.return_value = mock_root

        result = get_testplan_id_by_id([], "some", MagicMock(), exact=False)
        self.assertEqual(result, "some")


class TestRun(TestCase):
    @patch("checkbox_ng.launcher.subcommands.Explorer")
    def test__get_relevant_units(self, explorer_mock):
        self_mock = MagicMock()
        root = explorer_mock().get_object_tree()
        should_find = [
            "com.canonical.certification::some",
            "2021.com.canonica.certification::some",
        ]

        def find_children_by_name(pattern):
            if pattern == ["some"]:
                to_r = [MagicMock(), MagicMock()]
                to_r[0].name = should_find[0]
                to_r[1].name = should_find[1]
                return {"some": to_r}
            return {x: [] for x in pattern}

        root.find_children_by_name = find_children_by_name
        found_ids = Run._get_relevant_units(
            self_mock, ["other2.*", "some", "other1.*"], exact=False
        )

        # we expect the relevant unit function to leave unfound values the same
        # and all in the same order
        self.assertEqual(found_ids, ["other2.*", *should_find, "other1.*"])
