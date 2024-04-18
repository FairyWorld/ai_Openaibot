from abc import ABC
from typing import Optional, Union, List, Literal, Type

from docstring_parser import parse
from json_repair import repair_json
from pydantic import ConfigDict, BaseModel, Field, field_validator, model_validator


class FunctionChoice(BaseModel):
    name: str

    @classmethod
    def parse(
        cls,
        schema_model: Type[BaseModel],
        plugin_name: str = None,
    ):
        """
        解析 pydantic 的 schema
        :param schema_model:
        :param plugin_name:
        :return:
        """
        schema = schema_model.model_json_schema()
        plugin_name = plugin_name or schema["title"]
        return cls(
            name=plugin_name,
        )


class Function(FunctionChoice):
    class Parameters(BaseModel):
        type: str = "object"
        properties: dict = {}
        required: List[str] = Field(default=[], description="必填参数")
        model_config = ConfigDict(extra="ignore")

    name: str
    description: Optional[str]
    parameters: Parameters = None

    @classmethod
    def parse(
        cls,
        schema_model: Type[BaseModel],
        plugin_name: str = None,
    ):
        """
        解析 pydantic 的 schema
        """
        schema = schema_model.model_json_schema()
        docstring = parse(schema.__doc__ or "")
        parameters = {
            k: v for k, v in schema.items() if k not in ("title", "description")
        }
        for param in docstring.params:
            name = param.arg_name
            description = param.description
            if (name in parameters["properties"]) and description:
                if "description" not in parameters["properties"][name]:
                    parameters["properties"][name]["description"] = description

        parameters["required"] = sorted(
            k for k, v in parameters["properties"].items() if "default" not in v
        )

        if "description" not in schema:
            if docstring.short_description:
                schema["description"] = docstring.short_description
            else:
                schema["description"] = (
                    f"Correctly extracted `{cls.__name__}` with all "
                    f"the required parameters with correct types"
                )
        plugin_name = plugin_name or schema["title"]
        return cls(
            name=plugin_name,
            description=schema["description"],
            parameters=parameters,
        )


class CommonTool(BaseModel):
    type: Union[Literal["function", "code_interpreter"], str] = "function"
    model_config = ConfigDict(extra="allow")


class ToolChoice(CommonTool):
    type: Literal["function"] = "function"
    function: Union[FunctionChoice, Type[BaseModel]]

    @field_validator("function")
    def check_function(cls, v):
        if isinstance(v, FunctionChoice):
            _re = v
        elif issubclass(v, BaseModel):
            _re = FunctionChoice.parse(v)
        else:
            raise ValueError(
                "function must be a pydantic model or a FunctionChoice object"
            )
        assert isinstance(_re, FunctionChoice), RuntimeError(
            "function must be a pydantic model or a FunctionChoice object+"
        )
        return _re


class Tool(ToolChoice):
    type: Literal["function"] = "function"
    function: Union[Function, Type[BaseModel]]

    @field_validator("function")
    def check_function(cls, v):
        if isinstance(v, Function):
            _re = v
        elif issubclass(v, BaseModel):
            _re = Function.parse(v)
        else:
            raise ValueError("function must be a pydantic model or a Function object")
        assert isinstance(_re, Function), RuntimeError(
            "function must be a pydantic model or a Function object+"
        )
        return _re


class FunctionCalled(BaseModel):
    name: str
    arguments: Union[str] = None

    @property
    def json_arguments(self):
        arguments = self.arguments
        if isinstance(self.arguments, str):
            json_repaired = repair_json(self.arguments, return_objects=True)
            if not json_repaired:
                arguments = {}
            else:
                arguments = json_repaired
        return arguments


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCalled

    @property
    def name(self):
        return self.function.name

    @property
    def arguments(self):
        return self.function.json_arguments


class Message(BaseModel, ABC):
    role: Literal["user", "assistant"] = "user"
    content: str

    model_config = ConfigDict(extra="allow")


class SystemMessage(Message):
    role: Literal["system"] = "system"
    content: str
    name: Optional[str] = None


class UserMessage(Message):
    role: Literal["user"] = "user"
    content: str
    name: Optional[str] = None


class AssistantMessage(Message):
    role: Literal["assistant"] = "assistant"
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None

    @model_validator(mode="after")
    def check_tool_calls(self):
        if self.content is None and self.tool_calls is None:
            raise ValueError("content and tool_calls cannot be both None")
        if self.tool_calls is None:
            if self.content is None:
                self.content = ""
        return self


class ToolMessage(Message):
    role: Literal["tool"] = "tool"
    content: str
    tool_call_id: str


def active_cell(message: Union[dict, Message]) -> Optional[Message]:
    """
    激活消息
    :param message: message dict or Message object
    :return: Message or None
    :raises ValueError: message is not a valid message dict | message must have a 'role' key
    :raises NotImplementedError: role is not supported
    """
    if isinstance(message, Message):
        return message
    elif isinstance(message, dict):
        if message.get("role") is None:
            raise ValueError("message must have a 'role' key")
        if message["role"] == "system":
            return SystemMessage.model_validate(message)
        elif message["role"] == "user":
            return UserMessage.model_validate(message)
        elif message["role"] == "assistant":
            return AssistantMessage.model_validate(message)
        elif message["role"] == "tool":
            return ToolMessage.model_validate(message)
        else:
            raise NotImplementedError(f"role {message.get('role')} is not supported")
    else:
        raise ValueError("message is not a valid message dict")


def active_cell_string(message: str) -> Optional[Message]:
    """
    从字符串中激活消息
    :param message: message json string
    :return: Message or None
    :raises ValueError: message is not a valid message string
    :raises NotImplementedError: role is not supported
    """
    message = repair_json(message, return_objects=True)
    if not message:
        return None
    return active_cell(message)


def class_tool(tool: Union[Tool, Type[BaseModel]]) -> Tool:
    """
    从类中激活工具
    :param tool: Tool object
    :return: tool
    :raises ValueError: tool is not a valid Tool object
    """
    if isinstance(tool, Tool):
        return tool
    if issubclass(tool, BaseModel):
        tool = Tool(function=tool)
        return tool
    raise ValueError("tool is not a valid Tool object")
