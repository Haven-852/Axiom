class WorkflowConflict(Exception):
    """The project or repository already has a writer that has not finished."""


class AgentError(Exception):
    code = "agent_error"


class InputValidationError(AgentError):
    code = "input_invalid"


class AgentNotFoundError(AgentError):
    code = "agent_not_found"


class AgentExecutionError(AgentError):
    code = "agent_execution_failed"


class AgentTimeoutError(AgentError):
    code = "agent_timeout"


class AgentOutputValidationError(AgentError):
    code = "output_invalid"


class AgentCancelledError(AgentError):
    code = "agent_cancelled"


class AgentAlreadyRegistered(AgentError):
    code = "agent_already_registered"


class ProjectReadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
