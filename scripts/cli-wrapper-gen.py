import jinja2
import yaml
import sys
import re

def select_arguments(items):
    arguments = []
    for item in items:
        if not item["name"].startswith("--") and not item["name"].startswith("-"):
            arguments.append(item)
    return arguments


def select_parameters(items):
    parameters = []
    for item in items:
        if item["name"].startswith("--") or item["name"].startswith("-"):
            parameters.append(item)
    return parameters


def no_newline(text):
    return re.sub("\n", "", text)


def required(item):
    if "action" in item:
        return False
    elif "default" in item:
        return False
    elif "private" in item and item["private"]:
        return False
    elif "required" in item and item["required"]:
        return True
    elif not item["name"].startswith("--"):
        return True
    return False


def data_type_name(item):
    if "action" in item:
        return "marker"
    text = item["type"]
    if text == "str":
        return "string"
    elif text == "int":
        return "integer"
    elif text == "bool":
        return "boolean"
    else:
        return "unknown"

def escape_python_string(text):
    return text.replace('%', '%%')


def escape_strings(text):
    text = re.sub("'", "\\'", text)
    text = re.sub("\n", "", text)
    return text


def make_identifier(name):
    if name.startswith("--"):
        name = name[2:]
    return re.sub("-", "_", name.lower())


def bool_value(value):
    return value == "true"


def default_value(item):
    type = item["type"]
    if not "default" in item:
        return "None"
    value = item["default"]
    if type == "str":
        return "'%s'" % value
    elif type == "int":
        return value
    elif type == "bool":
        return value if isinstance(value, bool) else f"{value.lower()}" == "true"
    else:
        raise "unknown data type %s" % type


def split_value_range(value):
    return re.sub("\\.\\.", ', ', value)


def arg_value(item):
    if "action" in item:
        action = item["action"]
        if action == "store_true" or action == "store_false":
            return ""

    name = make_identifier(item["name"])
    return "=<%s>" % name


def get_description(item):
    if "description" in item:
        return no_newline(item["description"])
    elif "usage" in item:
        return no_newline(item["usage"])
    elif "help" in item:
        return no_newline(item["help"])
    else:
        return "<missing documentation>"


base_path = sys.argv[1]
with open("%s/cli-reference.yaml" % base_path) as stream:
    try:
        reference = yaml.safe_load(stream)

        for command in reference["commands"]:
            for subcommand in command["subcommands"]:
                if "arguments" in subcommand:
                    for argument in subcommand["arguments"]:
                        argument["required"] = False if "default" not in argument else True
                    arguments = select_arguments(subcommand["arguments"])
                    parameters = select_parameters(subcommand["arguments"])
                    subcommand["arguments"] = arguments
                    subcommand["parameters"] = parameters

        templateLoader = jinja2.FileSystemLoader(searchpath="%s/scripts/" % base_path)
        environment = jinja2.Environment(loader=templateLoader)

        environment.filters["no_newline"] = no_newline
        environment.filters["data_type_name"] = data_type_name
        environment.filters["default_value"] = default_value
        environment.filters["required"] = required
        environment.filters["get_description"] = get_description
        environment.filters["escape_strings"] = escape_strings
        environment.filters["make_identifier"] = make_identifier
        environment.filters["bool_value"] = bool_value
        environment.filters["split_value_range"] = split_value_range
        environment.filters["escape_python_string"] = escape_python_string

        template = environment.get_template("cli-wrapper.jinja2")
        output = template.render({"commands": reference["commands"]})
        with open("%s/simplyblock_cli/cli.py" % base_path, "t+w") as target:
            target.write(output)

        print("Successfully generated cli.py")

    except yaml.YAMLError as exc:
        print(exc)
