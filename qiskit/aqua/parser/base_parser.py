# -*- coding: utf-8 -*-

# Copyright 2019 IBM.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =============================================================================

from abc import ABC, abstractmethod
import json
from collections import OrderedDict
import logging
import copy
from qiskit.aqua import (local_pluggables_types,
                         PluggableType,
                         get_pluggable_configuration,
                         local_pluggables,
                         get_backends_from_provider)
from qiskit.aqua.aqua_error import AquaError
from .jsonschema import JSONSchema
import traceback


def exception_to_string(excp):
    stack = traceback.extract_stack()[:-3] + traceback.extract_tb(excp.__traceback__)
    pretty = traceback.format_list(stack)
    return ''.join(pretty) + '\n  {} {}'.format(excp.__class__, excp)


logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """Base Aqua Parser."""

    _UNKNOWN = 'unknown'
    _DEFAULT_PROPERTY_ORDER = [JSONSchema.NAME, _UNKNOWN]
    _BACKEND_PROPERTY_ORDER = [JSONSchema.PROVIDER, JSONSchema.NAME, _UNKNOWN]

    def __init__(self, jsonSchema):
        """Create InputParser object."""
        self._original_sections = None
        self._filename = None
        self._sections = None
        self._json_schema = jsonSchema
        self._json_schema._initialize_problem_section()
        self._json_schema.commit_changes()

    def _order_sections(self, sections):
        sections_sorted = OrderedDict(sorted(list(sections.items()),
                                             key=lambda x: self._section_order.index(x[0])
                                             if x[0] in self._section_order else self._section_order.index(BaseParser._UNKNOWN)))

        for section, properties in sections_sorted.items():
            if isinstance(properties, dict):
                _property_order = BaseParser._BACKEND_PROPERTY_ORDER if section == JSONSchema.BACKEND else BaseParser._DEFAULT_PROPERTY_ORDER
                sections_sorted[section] = OrderedDict(sorted(list(properties.items()),
                                                              key=lambda x: _property_order.index(x[0])
                                                              if x[0] in _property_order
                                                              else _property_order.index(BaseParser._UNKNOWN)))

        return sections_sorted

    @property
    def json_schema(self):
        """Getter of _json_schema."""
        return self._json_schema

    @abstractmethod
    def parse(self):
        """Parse the data."""
        pass

    def is_modified(self):
        """
        Returns true if data has been changed
        """
        return self._original_sections != self._sections

    @staticmethod
    def is_pluggable_section(section_name):
        section_name = JSONSchema.format_section_name(section_name)
        for pluggable_type in local_pluggables_types():
            if section_name == pluggable_type.value:
                return True

        return False

    def get_section_types(self, section_name):
        return self._json_schema.get_section_types(section_name)

    def get_property_types(self, section_name, property_name):
        return self._json_schema.get_property_types(section_name, property_name)

    @abstractmethod
    def get_default_sections(self):
        pass

    def get_default_section_names(self):
        sections = self.get_default_sections()
        return list(sections.keys()) if sections is not None else []

    def get_section_default_properties(self, section_name):
        return self._json_schema.get_section_default_properties(section_name)

    def allows_additional_properties(self, section_name):
        return self._json_schema.allows_additional_properties(section_name)

    def get_property_default_values(self, section_name, property_name):
        return self._json_schema.get_property_default_values(section_name, property_name)

    def get_property_default_value(self, section_name, property_name):
        return self._json_schema.get_property_default_value(section_name, property_name)

    def get_filename(self):
        """Return the filename."""
        return self._filename

    @staticmethod
    def get_algorithm_problems(algo_name):
        return JSONSchema.get_algorithm_problems(algo_name)

    def _merge_dependencies(self):
        algo_name = self.get_section_property(PluggableType.ALGORITHM.value, JSONSchema.NAME)
        if algo_name is None:
            return

        config = get_pluggable_configuration(PluggableType.ALGORITHM, algo_name)
        pluggable_dependencies = config.get('depends', [])

        section_names = self.get_section_names()
        for pluggable_type_dict in pluggable_dependencies:
            pluggable_type = pluggable_type_dict.get('pluggable_type')
            if pluggable_type is None:
                continue

            pluggable_name = None
            pluggable_defaults = pluggable_type_dict.get('default')
            new_properties = {}
            if pluggable_defaults is not None:
                for key, value in pluggable_defaults.items():
                    if key == JSONSchema.NAME:
                        pluggable_name = value
                    else:
                        new_properties[key] = value

            if pluggable_name is None:
                continue

            if pluggable_type not in section_names:
                self.set_section(pluggable_type)

            if self.get_section_property(pluggable_type, JSONSchema.NAME) is None:
                self.set_section_property(pluggable_type, JSONSchema.NAME, pluggable_name)

            if pluggable_name == self.get_section_property(pluggable_type, JSONSchema.NAME):
                properties = self.get_section_properties(pluggable_type)
                if new_properties:
                    new_properties.update(properties)
                else:
                    new_properties = properties

                self.set_section_properties(pluggable_type, new_properties)

    @abstractmethod
    def validate_merge_defaults(self):
        self.merge_default_values()
        self._json_schema.validate(self.get_sections())
        self._validate_algorithm_problem()

    @abstractmethod
    def merge_default_values(self):
        pass

    def _validate_algorithm_problem(self):
        algo_name = self.get_section_property(PluggableType.ALGORITHM.value, JSONSchema.NAME)
        if algo_name is None:
            return

        problem_name = self.get_section_property(JSONSchema.PROBLEM, JSONSchema.NAME)
        if problem_name is None:
            problem_name = self.get_property_default_value(JSONSchema.PROBLEM, JSONSchema.NAME)

        if problem_name is None:
            raise AquaError("No algorithm 'problem' section found on input.")

        problems = BaseParser.get_algorithm_problems(algo_name)
        if problem_name not in problems:
            raise AquaError("Problem: {} not in the list of problems: {} for algorithm: {}.".format(problem_name, problems, algo_name))

    def commit_changes(self):
        self._original_sections = copy.deepcopy(self._sections)

    @abstractmethod
    def save_to_file(self, file_name):
        pass

    def section_is_text(self, section_name):
        section_name = JSONSchema.format_section_name(section_name).lower()
        types = self.get_section_types(section_name)
        if len(types) > 0:
            return 'object' not in types

        section = self._sections.get(section_name)
        if section is None:
            return False

        return not isinstance(section, dict)

    def get_sections(self):
        return self._sections

    def get_section(self, section_name):
        """Return a Section by name.
        Args:
            section_name (str): the name of the section, case insensitive
        Returns:
            Section: The section with this name
        Raises:
            AquaError: if the section does not exist.
        """
        section_name = JSONSchema.format_section_name(section_name).lower()
        try:
            return self._sections[section_name]
        except KeyError:
            raise AquaError('No section "{0}"'.format(section_name))

    def get_section_text(self, section_name):
        section_name = JSONSchema.format_section_name(section_name).lower()
        section = self._sections.get(section_name)
        if section is None:
            return ''

        if isinstance(section, str):
            return section

        return json.dumps(section, sort_keys=True, indent=4)

    def get_section_properties(self, section_name):
        section_name = JSONSchema.format_section_name(section_name).lower()
        section = self._sections.get(section_name)
        if section is None:
            return {}

        return section

    def get_section_property(self, section_name, property_name, default_value=None):
        """Return a property by name.
        Args:
            section_name (str): the name of the section, case insensitive
            property_name (str): the property name in the section
            default_value : default value in case it is not found
        Returns:
            Value: The property value
        """
        section_name = JSONSchema.format_section_name(section_name)
        property_name = JSONSchema.format_property_name(property_name)
        if section_name in self._sections:
            section = self._sections[section_name]
            if property_name in section:
                return section[property_name]

        return default_value

    def set_section(self, section_name):
        """
        Args:
            section_name (str): the name of the section, case insensitive
        Returns:
            Bool: True if updated
        """
        updated = False
        section_name = JSONSchema.format_section_name(section_name)
        if section_name not in self._sections:
            self._sections[section_name] = '' if self.section_is_text(section_name) else OrderedDict()
            self._sections = self._order_sections(self._sections)
            updated = True

        return updated

    @abstractmethod
    def delete_section(self, section_name):
        """
        Args:
            section_name (str): the name of the section, case insensitive
        Returns:
            Bool: True if deleted
        """
        section_name = JSONSchema.format_section_name(section_name).lower()
        if section_name not in self._sections:
            return False

        del self._sections[section_name]

        # update schema
        self._json_schema.rollback_changes()
        self._json_schema.update_backend_schema(self)
        self._json_schema.update_pluggable_schemas(self)
        return True

    def add_section_properties(self, section_name, new_properties):
        """
        Add new properties if don't exist, update the existent ones, other properties are unchanged
        Args:
            section_name (str): the name of the section, case insensitive
            new_properties (dict): property name: value
        Returns:
            Bool: True if changed
        """
        key_value_changed = False
        set_properties = copy.deepcopy(new_properties)

        # update backend provider first
        if JSONSchema.BACKEND == section_name and JSONSchema.PROVIDER in set_properties:
            if self._set_section_property_without_checking_defaults(section_name,
                                                                    JSONSchema.PROVIDER,
                                                                    set_properties[JSONSchema.PROVIDER]):
                key_value_changed = True
            del set_properties[JSONSchema.PROVIDER]

        # update name first
        if JSONSchema.NAME in set_properties:
            if self._set_section_property_without_checking_defaults(section_name,
                                                                    JSONSchema.NAME,
                                                                    set_properties[JSONSchema.NAME]):
                key_value_changed = True
            del set_properties[JSONSchema.NAME]

        # update remaining properties
        for property_name, value in set_properties.items():
            if self._set_section_property_without_checking_defaults(section_name,
                                                                    property_name,
                                                                    value):
                key_value_changed = True

        # nothing changed, return
        if not key_value_changed:
            return False

        # remove properties that are not valid for this section
        default_properties = self.get_section_default_properties(section_name)
        if isinstance(default_properties, dict):
            properties = self.get_section_properties(section_name)
            for p_name in list(properties.keys()):
                if p_name != JSONSchema.NAME and p_name not in default_properties:
                    self.delete_section_property(section_name, p_name)

        self._sections = self._order_sections(self._sections)
        return True

    def set_section_properties(self, section_name, new_properties):
        """
        Replace all old properties with new ones
        Args:
            section_name (str): the name of the section, case insensitive
            new_properties (dict): property name: value
        Returns:
            Bool: True if changed
        """
        old_properties = self.get_section_properties(section_name)
        set_properties = copy.deepcopy(new_properties)
        del_properties = []
        for key, value in old_properties.items():
            if key in set_properties:
                if value == set_properties[key]:
                    del set_properties[key]
            else:
                del_properties.append(key)

        key_value_changed = False

        # first delete
        for property_name in del_properties:
            if self.delete_section_property(section_name, property_name):
                key_value_changed = True

        # update backend provider first
        if JSONSchema.BACKEND == section_name and JSONSchema.PROVIDER in set_properties:
            if self._set_section_property_without_checking_defaults(section_name,
                                                                    JSONSchema.PROVIDER,
                                                                    set_properties[JSONSchema.PROVIDER]):
                key_value_changed = True
            del set_properties[JSONSchema.PROVIDER]

        # update name first
        if JSONSchema.NAME in set_properties:
            if self._set_section_property_without_checking_defaults(section_name,
                                                                    JSONSchema.NAME,
                                                                    set_properties[JSONSchema.NAME]):
                key_value_changed = True
            del set_properties[JSONSchema.NAME]

        # update remaining properties
        for property_name, value in set_properties.items():
            if self._set_section_property_without_checking_defaults(section_name,
                                                                    property_name,
                                                                    value):
                key_value_changed = True

        # nothing changed, return
        if not key_value_changed:
            return False

        self._sections = self._order_sections(self._sections)
        return True

    @abstractmethod
    def post_set_section_property(self, section_name, property_name):
        pass

    def set_section_property(self, section_name, property_name, value):
        """
        Args:
            section_name (str): the name of the section, case insensitive
            property_name (str): the name of the property
            value (obj): the value of the property
        Returns:
            Bool: True if value changed
        """
        if not self._set_section_property_without_checking_defaults(section_name, property_name, value):
            return False

        # remove properties that are not valid for this section
        default_properties = self.get_section_default_properties(section_name)
        if isinstance(default_properties, dict):
            properties = self.get_section_properties(section_name)
            for p_name in list(properties.keys()):
                if p_name != JSONSchema.NAME and p_name not in default_properties:
                    self.delete_section_property(section_name, p_name)

        self._sections = self._order_sections(self._sections)
        return True

    def _set_section_property_without_checking_defaults(self, section_name, property_name, value):
        """
        Args:
            section_name (str): the name of the section, case insensitive
            property_name (str): the name of the property
            value (obj): the value of the property
        Returns:
            Bool: True if value changed
        """
        section_name = JSONSchema.format_section_name(section_name).lower()
        property_name = JSONSchema.format_property_name(property_name)
        value = self._json_schema.check_property_value(section_name, property_name, value)
        types = self.get_property_types(section_name, property_name)
        sections_temp = copy.deepcopy(self._sections)
        BaseParser._set_section_property(sections_temp, section_name, property_name, value, types)
        msg = self._json_schema.validate_property(sections_temp, section_name, property_name)
        if msg is not None:
            raise AquaError("{}.{}: Value '{}': '{}'".format(section_name, property_name, value, msg))

        value_changed = False
        if section_name not in self._sections:
            value_changed = True
        elif property_name not in self._sections[section_name]:
            value_changed = True
        else:
            old_value = self.get_section_property(section_name, property_name)
            value_changed = (old_value != value)

        if not value_changed:
            # nothing changed
            return False

        # check if the provider/backend is loadable and valid
        backend_names = []
        if JSONSchema.BACKEND == section_name and property_name in [JSONSchema.PROVIDER, JSONSchema.NAME]:
            provider_name = value if property_name == JSONSchema.PROVIDER else self.get_section_property(section_name, JSONSchema.PROVIDER)
            backend_names = get_backends_from_provider(provider_name)
            if property_name == JSONSchema.NAME and value not in backend_names:
                raise AquaError("Backend '{}' not valid for provider: '{}' backends: '{}'".format(value, provider_name, backend_names))

        # update value internally
        BaseParser._set_section_property(self._sections, section_name, property_name, value, types)

        if JSONSchema.BACKEND == section_name and property_name in [JSONSchema.PROVIDER, JSONSchema.NAME]:
            if property_name == JSONSchema.PROVIDER:
                backend_name = self.get_section_property(section_name, JSONSchema.NAME)
                if backend_name not in backend_names:
                    # use first backend available in provider
                    backend_name = backend_names[0] if len(backend_names) > 0 else ''
                    BaseParser._set_section_property(self._sections, section_name, JSONSchema.NAME, backend_name, ['string'])

            self._json_schema.update_backend_schema(self)
        elif property_name == JSONSchema.NAME:
            if JSONSchema.PROBLEM == section_name:
                self._update_algorithm_problem()
            elif BaseParser.is_pluggable_section(section_name):
                self._json_schema.update_pluggable_schemas(self)
                self._update_dependency_sections(section_name)
            else:
                self.post_set_section_property(section_name, property_name)

        return True

    def _update_algorithm_problem(self):
        problem_name = self.get_section_property(JSONSchema.PROBLEM, JSONSchema.NAME)
        if problem_name is None:
            problem_name = self.get_property_default_value(JSONSchema.PROBLEM, JSONSchema.NAME)

        if problem_name is None:
            raise AquaError("No algorithm 'problem' section found on input.")

        algo_name = self.get_section_property(PluggableType.ALGORITHM.value, JSONSchema.NAME)
        if algo_name is not None and problem_name in BaseParser.get_algorithm_problems(algo_name):
            return

        for algo_name in local_pluggables(PluggableType.ALGORITHM):
            if problem_name in self.get_algorithm_problems(algo_name):
                # set to the first algorithm to solve the problem
                self.set_section_property(PluggableType.ALGORITHM.value, JSONSchema.NAME, algo_name)
                return

        # no algorithm solve this problem, remove section
        self.delete_section(PluggableType.ALGORITHM.value)

    def _update_dependency_sections(self, section_name):
        sections_to_be_deleted = []
        prop_name = self.get_section_property(section_name, JSONSchema.NAME)
        config = {} if prop_name is None else get_pluggable_configuration(section_name, prop_name)
        pluggable_dependencies = config.get('depends', [])
        if section_name == PluggableType.ALGORITHM.value:
            sections_to_be_deleted = [name for name in self.get_section_names()
                                      if name != PluggableType.INPUT.value and self.is_pluggable_section(name)]

            classical = config.get('classical', False)
            # update backend based on classical
            if classical:
                if JSONSchema.BACKEND in self._sections:
                    del self._sections[JSONSchema.BACKEND]
            else:
                if JSONSchema.BACKEND not in self._sections:
                    self.set_section_properties(JSONSchema.BACKEND, self.get_section_default_properties(JSONSchema.BACKEND))

        # update dependencies recursively
        self._update_dependencies(section_name, sections_to_be_deleted, pluggable_dependencies)

        # remove pluggable sections not in algorithm dependency list
        for name in sections_to_be_deleted:
            if name in self._sections:
                del self._sections[name]

        # reorder sections
        self._sections = self._order_sections(self._sections)

    def _update_dependencies(self, section_name, sections_to_be_deleted, pluggable_dependencies):
        # remove dependency pluggable type from sections to be deleted
        if section_name in sections_to_be_deleted:
            sections_to_be_deleted.remove(section_name)

        # update sections with dependencies recursevely
        for pluggable_type_dict in pluggable_dependencies:
            pluggable_type = pluggable_type_dict.get('pluggable_type')
            if pluggable_type is None:
                continue

            pluggable_name = None
            pluggable_defaults = pluggable_type_dict.get('default')
            if pluggable_defaults is not None:
                pluggable_name = pluggable_defaults.get(JSONSchema.NAME)

            if pluggable_name is not None:
                if pluggable_type not in self._sections:
                    self.set_section_property(pluggable_type, JSONSchema.NAME, pluggable_name)
                    # update default values for new dependency pluggable types
                    default_properties = self.get_section_default_properties(pluggable_type)
                    if isinstance(default_properties, dict):
                        self.set_section_properties(pluggable_type, default_properties)

                config = get_pluggable_configuration(pluggable_type, pluggable_name)
                self._update_dependencies(pluggable_type, sections_to_be_deleted, config.get('depends', []))

    @staticmethod
    def _set_section_property(sections, section_name, property_name, value, types):
        """
        Args:
            section_name (str): the name of the section, case insensitive
            property_name (str): the property name in the section
            value : property value
            types : schema types
        """
        section_name = JSONSchema.format_section_name(section_name)
        property_name = JSONSchema.format_property_name(property_name)
        value = JSONSchema.get_value(value, types)

        if JSONSchema.NAME == property_name and \
           (value is None or len(value) == 0) and \
           BaseParser.is_pluggable_section(section_name):
            raise AquaError("Unable to set pluggable '{}' name: Missing name.".format(section_name))

        if section_name not in sections:
            sections[section_name] = OrderedDict()

        # name should come first
        if JSONSchema.NAME == property_name and property_name not in sections[section_name]:
            new_dict = OrderedDict([(property_name, value)])
            new_dict.update(sections[section_name])
            sections[section_name] = new_dict
        else:
            sections[section_name][property_name] = value

    def delete_section_property(self, section_name, property_name):
        """
        Args:
            section_name (str): the name of the section, case insensitive
            property_name (str): the property name in the section
        Returns:
            Bool: True if deleted
        """
        section_name = JSONSchema.format_section_name(section_name)
        property_name = JSONSchema.format_property_name(property_name)
        if section_name in self._sections and property_name in self._sections[section_name]:
            del self._sections[section_name][property_name]
            return True

        return False

    def delete_section_properties(self, section_name):
        """
        Args:
            section_name (str): the name of the section, case insensitive
        Returns:
            Bool: True if deleted
        """
        section_name = JSONSchema.format_section_name(section_name).lower()
        if section_name in self._sections:
            del self._sections[section_name]
            return True

        return False

    def set_section_data(self, section_name, value):
        """
        Sets a section data.
        Args:
            section_name (str): the name of the section, case insensitive
            value : value to set
         Returns:
            Bool: True if updated
        """
        section_name = JSONSchema.format_section_name(section_name)
        self._sections[section_name] = self._json_schema.check_section_value(section_name, value)
        return True

    def get_section_names(self):
        """Return all the names of the sections."""
        return list(self._sections.keys())
