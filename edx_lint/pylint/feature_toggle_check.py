"""
Pylint plugin: checks that feature toggles are properly annotated.
"""

import os
import re

import pkg_resources

from code_annotations.base import AnnotationConfig
from code_annotations.find_static import StaticSearch
from pylint.checkers import BaseChecker, utils
from pylint.interfaces import IAstroidChecker

from .common import BASE_ID, check_visitors


def register_checkers(linter):
    """
    Register checkers.
    """
    linter.register_checker(FeatureToggleChecker(linter))
    linter.register_checker(FeatureToggleAnnotationChecker(linter))


class AnnotationLines:
    """
    AnnotationLines provides utility methods to work with a string in terms of
    lines.  As an example, it can convert a Call node into a list of its contents
    separated by line breaks.
    """

    # Regex searches for annotations like: # .. toggle
    _ANNOTATION_REGEX = re.compile(r"[\s]*#[\s]*\.\.[\s]*(toggle)")

    def __init__(self, module_node):
        """
        Arguments:
            module_node: The visited module node.
        """
        module_as_binary = module_node.stream().read()

        file_encoding = module_node.file_encoding
        if file_encoding is None:
            file_encoding = "UTF-8"

        module_as_string = module_as_binary.decode(file_encoding)
        self._list_of_string_lines = module_as_string.split("\n")

    def is_line_annotated(self, line_number):
        """
        Checks if the provided line number is annotated.
        """
        if line_number < 1 or self._line_count() < line_number:
            return False

        return bool(self._ANNOTATION_REGEX.match(self._get_line_contents(line_number)))

    def _line_count(self):
        """
        Gets the number of lines in the string.
        """
        return len(self._list_of_string_lines)

    def _get_line_contents(self, line_number):
        """
        Gets the line of text designated by the provided line number.
        """
        return self._list_of_string_lines[line_number - 1]


@check_visitors
class FeatureToggleChecker(BaseChecker):
    """
    Checks that feature toggles are properly annotated and best practices
    are followed.
    """

    __implements__ = (IAstroidChecker,)

    name = "feature-toggle-checker"

    TOGGLE_NOT_ANNOTATED_MESSAGE_ID = "feature-toggle-needs-doc"
    ILLEGAL_WAFFLE_MESSAGE_ID = "illegal-waffle-usage"

    _CHECK_CAPITAL_REGEX = re.compile(r"[A-Z]")
    _WAFFLE_TOGGLE_CLASSES = ("WaffleFlag", "WaffleSwitch", "CourseWaffleFlag")
    _ILLEGAL_WAFFLE_FUNCTIONS = ["flag_is_active", "switch_is_active"]

    msgs = {
        ("E%d40" % BASE_ID): (
            "feature toggle (%s) is missing annotation",
            TOGGLE_NOT_ANNOTATED_MESSAGE_ID,
            "feature toggle is missing annotation",
        ),
        ("E%d41" % BASE_ID): (
            "illegal waffle usage with (%s): use utility classes {}.".format(
                ", ".join(_WAFFLE_TOGGLE_CLASSES)
            ),
            ILLEGAL_WAFFLE_MESSAGE_ID,
            "illegal waffle usage: use utility classes {}.".format(
                ", ".join(_WAFFLE_TOGGLE_CLASSES)
            ),
        ),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lines = None

    def visit_module(self, node):
        """Parses the module code to provide access to comments."""
        self._lines = AnnotationLines(node)

    def check_waffle_class_annotated(self, node):
        """
        Check Call node for waffle class instantiation with missing annotations.
        """
        if not hasattr(node.func, "name"):
            return

        # Looking for class instantiation, so should start with a capital letter
        starts_with_capital = self._CHECK_CAPITAL_REGEX.match(node.func.name)
        if not starts_with_capital:
            return

        # Search for toggle classes that require an annotation
        if not node.func.name.endswith(self._WAFFLE_TOGGLE_CLASSES):
            return

        if not self._lines.is_line_annotated(node.lineno - 1):
            feature_toggle_name = "UNKNOWN"

            if node.keywords is not None:
                for node_key in node.keywords:
                    if node_key.arg == "flag_name":
                        feature_toggle_name = node_key.value.value

            if feature_toggle_name == "UNKNOWN":
                if len(node.args) >= 2:
                    feature_toggle_name = node.args[1].as_string()

            self.add_message(
                self.TOGGLE_NOT_ANNOTATED_MESSAGE_ID,
                args=(feature_toggle_name,),
                node=node,
            )

    def check_configuration_model_annotated(self, node):
        """
        Checks class definitions to see if they subclass ConfigurationModel.
        If they do, they should be correctly annotated.
        """
        if "ConfigurationModel" not in node.basenames:
            return
        if not self._lines.is_line_annotated(node.lineno - 1):
            config_model_subclass_name = node.name

            self.add_message(
                self.TOGGLE_NOT_ANNOTATED_MESSAGE_ID,
                args=(config_model_subclass_name,),
                node=node,
            )

    def check_django_feature_flag_annotated(self, node):
        """
        Checks dictionary definitions to see if the django feature flags
        dict FEATURES is being set. If it is, entries should be
        correctly annotated.
        """
        try:
            parent_target_name = node.parent.targets[0].name
        except AttributeError:
            return

        if parent_target_name == "FEATURES":
            for key, _ in node.items:
                if not self._lines.is_line_annotated(key.lineno - 1):
                    django_feature_toggle_name = key.value

                    self.add_message(
                        self.TOGGLE_NOT_ANNOTATED_MESSAGE_ID,
                        args=(django_feature_toggle_name,),
                        node=node,
                    )

    def check_illegal_waffle_usage(self, node):
        """
        Check Call node for illegal waffle calls.
        """
        if not hasattr(node.func, "name"):
            return

        if node.func.name in self._ILLEGAL_WAFFLE_FUNCTIONS:
            feature_toggle_name = "UNKNOWN"
            if len(node.args) >= 1:
                feature_toggle_name = node.args[0].as_string()

            self.add_message(
                self.ILLEGAL_WAFFLE_MESSAGE_ID, args=(feature_toggle_name,), node=node
            )

    @utils.check_messages(TOGGLE_NOT_ANNOTATED_MESSAGE_ID, ILLEGAL_WAFFLE_MESSAGE_ID)
    def visit_call(self, node):
        """
        Performs various checks on Call nodes.
        """
        self.check_waffle_class_annotated(node)
        self.check_illegal_waffle_usage(node)

    @utils.check_messages(TOGGLE_NOT_ANNOTATED_MESSAGE_ID)
    def visit_classdef(self, node):
        """
        Checks class definitions for potential ConfigurationModel
        implementations.
        """
        self.check_configuration_model_annotated(node)

    @utils.check_messages(TOGGLE_NOT_ANNOTATED_MESSAGE_ID)
    def visit_dict(self, node):
        """
        Checks Dict nodes in case a Django FEATURES dictionary is being
        initialized.
        """
        self.check_django_feature_flag_annotated(node)


@check_visitors
class FeatureToggleAnnotationChecker(BaseChecker):
    """
    Parse feature toggle annotations and ensure best practices are followed.
    """

    __implements__ = (IAstroidChecker,)

    name = "feature-toggle-annotation-checker"

    INCORRECT_NAME_MESSAGE_ID = "toggle-incorrect-name"
    EMPTY_DESCRIPTION_MESSAGE_ID = "toggle-empty-description"
    MISSING_TARGET_REMOVAL_DATE_MESSAGE_ID = "toggle-missing-target-removal-date"
    NON_BOOLEAN_DEFAULT_VALUE = "toggle-non-boolean-default-value"

    msgs = {
        ("E%d50" % BASE_ID): (
            "feature toggle has undefined or incorrectly placed name",
            INCORRECT_NAME_MESSAGE_ID,
            "Feature toggle name must be present and be the first annotation",
        ),
        ("E%d51" % BASE_ID): (
            "feature toggle (%s) does not have a description",
            EMPTY_DESCRIPTION_MESSAGE_ID,
            "Feature toggles must include a thorough description",
        ),
        ("E%d52" % BASE_ID): (
            "temporary feature toggle (%s) has no target removal date",
            MISSING_TARGET_REMOVAL_DATE_MESSAGE_ID,
            "Temporary feature toggles must include a target removal date",
        ),
        ("E%d53" % BASE_ID): (
            "feature toggle (%s) default value must be boolean ('True' or 'False')",
            NON_BOOLEAN_DEFAULT_VALUE,
            "Temporary feature toggles must include a target removal date",
        ),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config_path = pkg_resources.resource_filename(
            "code_annotations",
            os.path.join("contrib", "config", "feature_toggle_annotations.yaml"),
        )
        self.config = AnnotationConfig(config_path, verbosity=-1)
        self.search = StaticSearch(self.config)

    @utils.check_messages(
        INCORRECT_NAME_MESSAGE_ID,
        EMPTY_DESCRIPTION_MESSAGE_ID,
        MISSING_TARGET_REMOVAL_DATE_MESSAGE_ID,
        NON_BOOLEAN_DEFAULT_VALUE,
    )
    def visit_module(self, node):
        """
        Perform checks on all annotation groups for this module.
        """
        # This is a hack to avoid re-creating AnnotationConfig every time
        self.config.source_path = node.path[0]
        results = self.search.search()

        current_annotations_group = []
        for _file_name, results in results.items():
            for current_annotations_group in self.search.iter_groups(results):
                self.check_feature_toggles_annotation_group(current_annotations_group, node)

    def check_feature_toggles_annotation_group(self, annotations, node):
        """
        Perform checks on a single annotation group.
        """
        if not annotations:
            return

        target_removal_date = None
        temporary_use_case = False
        toggle_name = ""
        toggle_description = ""
        toggle_default = None
        for annotation in annotations:
            if annotation["annotation_token"] == ".. toggle_name:":
                toggle_name = annotation["annotation_data"]
            elif annotation["annotation_token"] == ".. toggle_description:":
                toggle_description = annotation["annotation_data"].strip()
            elif annotation["annotation_token"] == ".. toggle_use_cases:":
                if "temporary" in annotation["annotation_data"]:
                    temporary_use_case = True
            elif annotation["annotation_token"] == ".. toggle_target_removal_date:":
                target_removal_date = annotation["annotation_data"]
            elif annotation["annotation_token"] == ".. toggle_default:":
                toggle_default = annotation["annotation_data"]

        if not toggle_name:
            self.add_message(
                self.INCORRECT_NAME_MESSAGE_ID,
                node=node,
            )
        if not toggle_description:
            self.add_message(
                self.EMPTY_DESCRIPTION_MESSAGE_ID,
                args=(toggle_name,),
                node=node,
            )
        if temporary_use_case and not target_removal_date:
            self.add_message(
                self.MISSING_TARGET_REMOVAL_DATE_MESSAGE_ID,
                args=(toggle_name,),
                node=node,
            )
        if toggle_default not in ["True", "False"]:
            self.add_message(
                self.NON_BOOLEAN_DEFAULT_VALUE,
                args=(toggle_name,),
                node=node,
            )
