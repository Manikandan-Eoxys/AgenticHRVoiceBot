
# """
# agent.py

# HR Voice Agent
# LiveKit Agents v1.6.5
# """

# from livekit.agents import (
#     Agent,
#     RunContext,
#     function_tool,
# )
# from livekit.agents.llm import ChatContext, ChatMessage, StopResponse  # NEW

# from services.employee_service import EmployeeService
# from services.leave_service import LeaveService
# from services.grievance_service import GrievanceService
# from services.policy_service import PolicyService
# from services.calendar_service import CalendarService
# from memory.conversation_memory import ConversationMemory

# from security.auth_service import AuthService
# from security.auth_session import AuthSession
# # from pathlib import Path
# from config import SYSTEM_PROMPT
# from modules.topic_guard import classify as _classify_topic

# # ── NEW: Attendance & Workforce ──────────────────────────────────────────────
# from tools.attendance_tools import AttendanceTools

# # SYSTEM_PROMPT = Path(
# #     "prompts/system_prompt.txt"
# # ).read_text(encoding="utf-8")

# class HRAgent(Agent):

#     def __init__(self):

#         super().__init__(
#             instructions=SYSTEM_PROMPT
#         )

#         self.memory = ConversationMemory()

#         self.auth_service = AuthService()
#         self.auth = AuthSession()
        
#         self.employee_service = EmployeeService()
#         self.leave_service = LeaveService()
#         self.grievance_service = GrievanceService()
#         self.policy_service = PolicyService()
#         self.calendar_service = CalendarService()

#         # ── NEW: Attendance & Workforce tools ────────────────────────────────
#         self.attendance_tools = AttendanceTools()

#     async def on_enter(self):
#         """Greet the user when the session starts."""
#         await self.session.say(
#             "Hello! I'm HR Buddy, your Agentic HR Voice Assistant. "
#             "I can help you with leave requests, absence management, "
#             "team coverage, HR policies, and more. "
#             "How can I help you today?",
#             allow_interruptions=True,
#         )

#     # ------------------------------------------------------------------
#     # NEW: code-enforced HR-only guardrail.
#     #
#     # This runs the instant the user finishes speaking, BEFORE the LLM
#     # is invoked — so it can't be skipped by a model that decides not
#     # to call check_topic(). If the message is off-topic, we speak the
#     # refusal directly and raise StopResponse() so the LLM never runs
#     # for this turn at all. Otherwise we return and the normal flow
#     # (LLM sees the message, may call check_topic or any other tool)
#     # continues exactly as before.
#     # ------------------------------------------------------------------
#     async def on_user_turn_completed(
#         self,
#         turn_ctx: ChatContext,
#         new_message: ChatMessage,
#     ) -> None:
#         text = (new_message.text_content or "").strip()
#         if not text:
#             return

#         # NEW: if the assistant's last message was a follow-up question
#         # (employee ID, confirmation, dates, OTP, etc.), treat this reply
#         # as an in-context continuation and skip the guard. Without this,
#         # plain answers like "one zero zero one", "yes", or spoken dates
#         # get wrongly rejected because they contain no HR keyword on
#         # their own — even though they're clearly part of an HR task
#         # already in progress.
#         last_assistant_text = _last_assistant_message(turn_ctx)
#         if last_assistant_text and last_assistant_text.rstrip().endswith("?"):
#             return  # let the LLM handle it normally, no guard check needed

#         result = _classify_topic(text)

#         if not result["allowed"]:
#             await self.session.say(result["message"], allow_interruptions=True)
#             raise StopResponse()

#     ####################################################################
#     # GUARDRAIL
#     ####################################################################

#     @function_tool()
#     async def check_topic(
#         self,
#         context: RunContext,
#         user_message: str,
#     ):
#         """
#         MANDATORY GUARDRAIL — Call this tool FIRST whenever the user's request
#         is ambiguous, seems unrelated to HR, or touches any of the following:

#         - Weather, geography, science
#         - Politics, current events, news
#         - Finance, stocks, cryptocurrency
#         - Sports, entertainment, movies, music
#         - Recipes, food, restaurants
#         - Travel, tourism, hotels
#         - Programming, coding, software
#         - Medical or legal advice
#         - Jokes, stories, poems, creative writing
#         - General knowledge or trivia

#         This tool classifies the message and either:
#           - Returns allowed=True  -> proceed normally
#           - Returns allowed=False -> speak the 'message' field verbatim and stop

#         DO NOT skip this tool for borderline requests.
#         DO NOT answer non-HR questions even if you think you know the answer.
#         """
#         result = _classify_topic(user_message)
#         return result

#     ####################################################################
#     # EMPLOYEE
#     ####################################################################

#     @function_tool()
#     async def get_employee(
#         self,
#         context: RunContext,
#         employee_id: int,
#     ):
#         """
#         Retrieve complete information for an employee using their employee ID.

#         Use this tool whenever the user asks for details about a specific employee
#         and already provides the employee ID.

#         Examples:
#         - Show employee 1001.
#         - Get details of employee ID 1045.
#         - What is the information for employee 2005?

#         Do NOT use this tool if the user only provides a name.
#         Use search_employee instead.
#         """
#         return self.employee_service.get_employee(employee_id)
    

#     @function_tool()
#     async def search_employee(
#         self,
#         context: RunContext,
#         name: str,
#     ):
#         """
#         Search employees by first name or last name.

#         Use this tool whenever the user mentions an employee name instead of an ID.

#         Examples:
#         - Find John.
#         - Search employee named Alice.
#         - Do we have an employee called David?
#         - Show me Ravi.

#         Returns one or more matching employees.

#         Do NOT use when an employee ID is available.
#         """
#         return self.employee_service.search_employee(name)

#     @function_tool()
#     async def employee_summary(
#         self,
#         context: RunContext,
#         employee_id: int,
#     ):
#         """
#         Retrieve a short summary of an employee.

#         Includes information such as:
#         - Name
#         - Department
#         - Designation
#         - Manager
#         - Contact information

#         Use this when the user asks for an overview instead of complete employee details.

#         Examples:
#         - Give me a summary of employee 1005.
#         - Tell me about employee 1005.
#         """
#         return self.employee_service.get_employee_summary(employee_id)

#     @function_tool()
#     async def list_employees(
#         self,
#         context: RunContext,
#     ):
#         """
#         Retrieve a list of all employees.

#         Use only when the user explicitly asks to list employees.

#         Examples:
#         - List all employees.
#         - Show every employee.
#         - Display employee directory.
#         """
#         return self.employee_service.list_employees()

#     ####################################################################
#     # LEAVE
#     ####################################################################

#     # @function_tool()
#     # async def leave_balance(
#     #     self,
#     #     context: RunContext,
#     #     employee_id: int,
#     # ):
#     #     """
#     #     Retrieve the available leave balance for an employee.

#     #     Use whenever the user asks about remaining leave.

#     #     Examples:
#     #     - How many casual leaves do I have?
#     #     - What's my leave balance?
#     #     - Remaining annual leave?
#     #     - Sick leave left?

#     #     Requires an employee ID.
#     #     """
#     #     return self.leave_service.check_leave_balance(employee_id)
#     @function_tool()
#     async def leave_balance(
#         self,
#         context: RunContext,
#         employee_id: int | None = None,
#     ):
#         """
#         Retrieve the available leave balance for an employee.
#         Requires the employee to be verified first.
#         ...
#         """
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity first. What's your employee ID?"
#             }
#         # Use the verified identity — never trust an ID the model supplies
#         # separately, since that's exactly how balance for employee 123 got
#         # returned without anyone confirming they *were* employee 123.
#         employee_id = self.auth.employee_id
#         return self.leave_service.check_leave_balance(employee_id)

#     @function_tool()
#     async def apply_leave(
#         self,
#         context: RunContext,
#         employee_id: int,
#         from_date: str,
#         to_date: str,
#         leave_type: str,
#     ):
#         """
#         Submit a leave or absence request for an employee.

#         Delegates to the full absence pipeline which:
#           - Checks team coverage threshold
#           - Detects exception scenarios and routes to Area Manager
#           - Syncs to IFS Cloud and SQL Server

#         leave_type / absence_type must be one of:
#           'Sickness', 'Holiday', 'Emergency', 'Funeral',
#           'Compassionate', 'Casual', 'Earned'

#         Always ask for confirmation before calling this tool.

#         Examples:
#         - I want leave tomorrow.
#         - Apply casual leave.
#         - Book annual leave.
#         - I need holiday from Monday to Friday.
#         """
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity first. What is your employee ID?"
#             }
#         # Delegate to the full absence pipeline (coverage check, exception
#         # detection, IFS/SQL sync) rather than the legacy LeaveService path.
#         return self.attendance_tools.submit_absence_request(
#             employee_id  = employee_id,
#             absence_type = leave_type,
#             from_date    = from_date,
#             to_date      = to_date,
#             reason       = "",
#         )

#     @function_tool()
#     async def cancel_leave(
#         self,
#         context: RunContext,
#         request_id: int,
#     ):
#         # -------------------------
#         # Authentication Check
#         # -------------------------
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity first by providing your employee ID."
#             }

#         """
#         Cancel an existing leave request.

#         Use only after the employee provides the leave request ID.

#         Examples:
#         - Cancel my leave.
#         - Withdraw leave request 25.
#         - Delete my leave application.

#         Requires authentication.
#         """
#         return self.leave_service.cancel_leave(request_id)

#     @function_tool()
#     async def leave_history(
#         self,
#         context: RunContext,
#         employee_id: int,
#     ):
#         """
#         Retrieve the leave history for an employee.

#         Use when the employee asks about previous leave requests.

#         Examples:
#         - Show my leave history.
#         - Previous leave applications.
#         - What leave have I taken this year?
#         """
#         return self.leave_service.leave_history(employee_id)

#     ####################################################################
#     # GRIEVANCE
#     ####################################################################

#     @function_tool()
#     async def raise_grievance(
#         self,
#         context: RunContext,
#         employee_id: int,
#         category: str,
#         description: str,
#         anonymous: bool = False,
#     ):
#         # -------------------------
#         # Authentication Check
#         # -------------------------
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity first by providing your employee ID."
#             }

#         """
#         Create a new employee grievance.

#         Use only after collecting:

#         - Employee ID
#         - Category
#         - Description
#         - Anonymous or not

#         Always ask for confirmation before submitting.

#         Examples:
#         - I want to report harassment.
#         - Raise a grievance.
#         - Report a workplace issue.
#         - File a complaint.

#         Requires authentication.
#         """
#         return self.grievance_service.create_grievance(
#             employee_id,
#             category,
#             description,
#             anonymous,
#         )

#     @function_tool()
#     async def grievance_status(
#         self,
#         context: RunContext,
#         grievance_id: int,
#     ):
#         """
#         Retrieve the status of a grievance using its grievance ID.

#         Examples:
#         - Check grievance 45.
#         - What's the status of my complaint?
#         - Has grievance 20 been resolved?
#         """
#         return self.grievance_service.get_grievance(grievance_id)

#     @function_tool()
#     async def employee_grievances(
#         self,
#         context: RunContext,
#         employee_id: int,
#     ):
#         """
#         Retrieve all grievances submitted by an employee.

#         Examples:
#         - Show my grievances.
#         - List my complaints.
#         - My grievance history.
#         """
#         return self.grievance_service.list_employee_grievances(employee_id)

#     @function_tool()
#     async def escalate_grievance(
#         self,
#         context: RunContext,
#         grievance_id: int,
#     ):
#         # -------------------------
#         # Authentication Check
#         # -------------------------
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity first by providing your employee ID."
#             }

#         """
#         Escalate an existing grievance.

#         Use only after confirming with the employee.

#         Examples:
#         - Escalate grievance 24.
#         - I want HR management to review my complaint.

#         Requires authentication.
#         """
#         return self.grievance_service.escalate_grievance(grievance_id)

#     ####################################################################
#     # POLICY
#     ####################################################################

#     @function_tool()
#     async def get_policy(
#         self,
#         context: RunContext,
#         policy_name: str,
#     ):
#         """
#         Retrieve the full HR policy for a specific topic.

#         Use when the user asks about one known policy.

#         Examples:
#         - Explain maternity leave policy.
#         - What is the work from home policy?
#         - Show me the attendance policy.
#         - Explain casual leave policy.

#         Never invent policy information.
#         Always use this tool.
#         """
#         return self.policy_service.get_policy(policy_name)

#     @function_tool()
#     async def search_policy(
#         self,
#         context: RunContext,
#         keyword: str,
#     ):
#         """
#         Search HR policies using keywords.

#         Use when the user is unsure of the exact policy name.

#         Examples:
#         - Policies related to leave.
#         - Travel policies.
#         - Remote work.
#         - Insurance.
#         - Performance review.

#         Return matching policies.
#         """
#         return self.policy_service.search_policy(keyword)

#     @function_tool()
#     async def leave_eligibility(
#         self,
#         context: RunContext,
#         leave_type: str,
#         years_of_service: int,
#     ):
#         """
#         Check whether an employee is eligible for a specific leave type.

#         Requires:

#         - Leave type
#         - Years of service

#         Examples:
#         - Am I eligible for maternity leave?
#         - Can a new employee take earned leave?
#         - Eligibility for paternity leave.
#         """
#         return self.policy_service.check_leave_eligibility(
#             leave_type,
#             years_of_service,
#         )

#     @function_tool()
#     async def list_policies(
#         self,
#         context: RunContext,
#     ):
#         """
#         Retrieve a list of all available HR policies.

#         Use only when the employee asks to browse policies.

#         Examples:
#         - List all HR policies.
#         - What policies are available?
#         - Show company policies.
#         """
#         return self.policy_service.list_policies()

#     ####################################################################
#     # CALENDAR
#     ####################################################################

#     @function_tool()
#     async def schedule_meeting(
#         self,
#         context: RunContext,
#         employee_id: int,
#         title: str,
#         event_date: str,
#         event_time: str,
#         duration: int = 30,
#     ):
#         # -------------------------
#         # Authentication Check
#         # -------------------------
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity first by providing your employee ID."
#             }

#         """
#         Schedule a meeting.

#         Collect:

#         - Employee ID
#         - Meeting title
#         - Date
#         - Time
#         - Duration

#         Always confirm before scheduling.

#         Examples:
#         - Schedule a meeting tomorrow.
#         - Book a meeting with HR.
#         - Create an interview meeting.

#         Requires authentication.
#         """
#         return self.calendar_service.schedule_meeting(
#             employee_id,
#             title,
#             event_date,
#             event_time,
#             duration,
#         )

#     @function_tool()
#     async def cancel_meeting(
#         self,
#         context: RunContext,
#         event_id: int,
#     ):
#         # -------------------------
#         # Authentication Check
#         # -------------------------
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity first by providing your employee ID."
#             }

#         """
#         Cancel an existing meeting.

#         Requires the meeting ID.

#         Examples:
#         - Cancel meeting 35.
#         - Delete tomorrow's meeting.

#         Requires authentication.
#         """
#         return self.calendar_service.cancel_meeting(event_id)

#     @function_tool()
#     async def upcoming_meetings(
#         self,
#         context: RunContext,
#         employee_id: int,
#     ):
#         """
#         Retrieve all upcoming meetings for an employee.

#         Examples:
#         - My meetings.
#         - What's on my calendar?
#         - Upcoming meetings.
#         """
#         return self.calendar_service.upcoming_meetings(employee_id)

#     @function_tool()
#     async def available_slots(
#         self,
#         context: RunContext,
#         employee_id: int,
#         event_date: str,
#     ):
#         """
#         Retrieve available meeting time slots for a specific day.

#         Requires:

#         - Employee ID
#         - Date

#         Examples:
#         - Free slots tomorrow.
#         - When am I available on Monday?
#         - Available meeting times.
#         """
#         return self.calendar_service.available_slots(
#             employee_id,
#             event_date,
#         )

#     ####################################################################
#     # SECURITY
#     ####################################################################
    
#     @function_tool()
#     async def verify_employee(
#         self,
#         context: RunContext,
#         employee_id: int | None = None,   # ← must have the default
#     ):
#         """
#         Begin employee identity verification.

#         Use before any operation that modifies company data.

#         Examples:
#         - Apply leave.
#         - Raise grievance.
#         - Cancel leave.
#         - Schedule meeting.
#         - Update employee information.

#         The employee provides an employee ID.

#         This tool generates an OTP for verification.
#         """
#         if employee_id is None:
#             return {
#                 "success": False,
#                 "message": "What's your employee ID?",
#             }

#         result = self.auth_service.begin_verification(employee_id)

#         if not result["success"]:

#             return result

#         self.auth.employee_id = employee_id

#         # TEMPORARY defensive fix: begin_verification()'s actual return
#         # shape doesn't match what this line originally assumed
#         # (result["employee"]["name"]), which was causing a KeyError and
#         # crashing the tool call. Using .get() prevents the crash, but
#         # employee_name may end up empty until we confirm the real key
#         # from auth_service.py and fix this properly.
#         self.auth.employee_name = (
#             result.get("employee", {}).get("name")
#             or result.get("employee_name")
#             or result.get("name")
#             or "Employee"
#         )

#         self.auth.verification_pending = True

#         self.auth.otp_sent = True

#         return {

#             "success": True,

#             "message":
#             f"For demo purposes, your OTP is {result['otp']}. Please provide this OTP to continue."

#         }

#     @function_tool()
#     async def verify_otp(
#         self,
#         context: RunContext,
#         otp: str,
#     ):
#         """
#         Verify the OTP previously sent to the employee.

#         Use immediately after verify_employee.

#         Examples:
#         - My OTP is 453281.
#         - Verify 884522.
#         - The code is 111222.

#         After successful verification, the employee is authenticated.
#         """

#         if self.auth.employee_id is None:

#             return {

#                 "success": False,

#                 "message": "No employee verification is in progress."

#             }

#         ok = self.auth_service.verify_otp(

#             self.auth.employee_id,

#             otp,

#         )

#         if not ok["success"]:

#             return {

#                 "success": False,

#                 "message": "Invalid OTP."

#             }

#         self.auth.login(

#             self.auth.employee_id,

#             self.auth.employee_name,

#         )

#         return {

#             "success": True,

#             "message":
#             f"Welcome {self.auth.employee_name}. Your identity has been verified."

#         }


#     ####################################################################
#     # ATTENDANCE MANAGEMENT (NEW)
#     ####################################################################

#     @function_tool()
#     async def submit_absence_request(
#         self,
#         context: RunContext,
#         employee_id: int,
#         absence_type: str,
#         from_date: str,
#         to_date: str,
#         reason: str = "",
#     ):
#         """
#         Submit an absence request for a field engineer.

#         REQUIRES AUTHENTICATION before calling.

#         absence_type must be one of:
#           'Sickness'  — calling in sick on the day
#           'Holiday'   — planned annual / casual holiday
#           'Emergency' — unexpected emergency (short notice)
#           'Funeral'   — bereavement or funeral leave
#           'Casual'    — casual day off
#           'Earned'    — earned / annual leave

#         from_date and to_date must be in YYYY-MM-DD format.

#         The system will automatically:
#           - Check team coverage (80% minimum threshold)
#           - Detect if the request requires Area Manager approval
#           - Route exceptions to the Area Manager
#           - Update IFS Cloud and SQL Server after approval

#         Examples:
#           - I'm sick today, please log it.
#           - I want to book holiday from 2026-08-05 to 2026-08-07.
#           - Emergency leave for tomorrow.
#           - My father passed away, I need funeral leave.
#         """
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity first. What is your employee ID?"
#             }
#         return self.attendance_tools.submit_absence_request(
#             employee_id  = employee_id,
#             absence_type = absence_type,
#             from_date    = from_date,
#             to_date      = to_date,
#             reason       = reason,
#         )

#     @function_tool()
#     async def check_team_coverage(
#         self,
#         context: RunContext,
#         field_manager_id: int,
#         coverage_date: str,
#     ):
#         """
#         Check the attendance coverage percentage for a Field Manager's team on a given date.

#         Returns:
#           - Total engineers in the team
#           - Number available (not on leave)
#           - Number absent (on approved/pending leave)
#           - Coverage percentage
#           - Whether coverage is above the 80% minimum threshold
#           - A list of each engineer's status

#         Use when a Field Manager asks:
#           - How many of my team are in today?
#           - What is my team's coverage on Monday?
#           - Can I approve another absence?
#           - Are we below threshold for this week?

#         coverage_date must be in YYYY-MM-DD format.
#         """
#         return self.attendance_tools.check_team_coverage(
#             field_manager_id = field_manager_id,
#             coverage_date    = coverage_date,
#         )

#     @function_tool()
#     async def get_team_roster(
#         self,
#         context: RunContext,
#         field_manager_id: int,
#         roster_date: str,
#     ):
#         """
#         Retrieve the full daily roster for a Field Manager's team.

#         Shows each engineer's shift status:
#           - Scheduled (available)
#           - OnLeave (absence approved)
#           - The type of leave if absent

#         Use when a Field Manager asks:
#           - Show me the roster for Monday.
#           - Who is in tomorrow?
#           - Which engineers are on leave this week?

#         roster_date must be in YYYY-MM-DD format.
#         """
#         return self.attendance_tools.get_team_roster(
#             field_manager_id = field_manager_id,
#             roster_date      = roster_date,
#         )

#     @function_tool()
#     async def list_pending_exceptions(
#         self,
#         context: RunContext,
#         area_manager_id: int,
#     ):
#         """
#         Retrieve all exception absence requests that are pending the Area Manager's decision.

#         Returns each exception with:
#           - Exception ID
#           - Employee name and dates
#           - Type of exception (e.g. FuneralLeave, CoverageThresholdBreach)
#           - Reason for the exception

#         Use when an Area Manager asks:
#           - What exceptions are waiting for my approval?
#           - Show me pending absence approvals.
#           - Do I have any requests to review?
#         """
#         return self.attendance_tools.list_pending_exceptions(
#             area_manager_id = area_manager_id,
#         )

#     @function_tool()
#     async def approve_exception(
#         self,
#         context: RunContext,
#         exception_id: int,
#         area_manager_id: int,
#         decision: str,
#         notes: str = "",
#     ):
#         """
#         Area Manager approves or rejects an exception absence request.

#         REQUIRES AUTHENTICATION.

#         decision must be 'Approved' or 'Rejected'.
#         notes: optional reason or comments.

#         After the decision:
#           - The employee is notified automatically.
#           - If Approved: IFS Cloud and SQL Server are updated.
#           - If Approved: Roster is automatically reallocated.
#           - If Rejected: The employee's leave balance is restored.

#         Examples:
#           - Approve exception 2.
#           - Reject exception 3 — insufficient notice given.
#           - I approve the funeral leave request.
#         """
#         if not self.auth.authenticated:
#             return {
#                 "success": False,
#                 "message": "Please verify your identity as an Area Manager first."
#             }
#         return self.attendance_tools.approve_exception(
#             exception_id    = exception_id,
#             area_manager_id = area_manager_id,
#             decision        = decision,
#             notes           = notes,
#         )

#     @function_tool()
#     async def get_all_teams_coverage(
#         self,
#         context: RunContext,
#         target_date: str,
#     ):
#         """
#         Get a coverage summary across ALL Field Manager teams for a given date.

#         Useful for Area Managers and operations staff to spot teams at risk of
#         falling below the attendance threshold.

#         Returns for each team:
#           - Field Manager name
#           - Total engineers / available / absent
#           - Coverage percentage
#           - Whether they are below threshold

#         Examples:
#           - How are all teams covered today?
#           - Which teams are at risk this week?
#           - Give me the operations coverage overview for Monday.

#         target_date must be in YYYY-MM-DD format.
#         """
#         return self.attendance_tools.get_all_teams_coverage(
#             target_date = target_date,
#         )

#     @function_tool()
#     async def get_exception_detail(
#         self,
#         context: RunContext,
#         exception_id: int,
#     ):
#         """
#         Retrieve full details of a specific exception absence request.

#         Returns:
#           - Employee name and dates
#           - Exception type and reason
#           - Area Manager assigned and current status
#           - AM decision and notes (if decided)

#         Use when an Area Manager or Field Manager asks:
#           - Tell me about exception 3.
#           - What is the detail of exception request 7?
#           - Show me the exception for employee 1023.
#         """
#         return self.attendance_tools.get_exception_detail(
#             exception_id = exception_id,
#         )


# def _last_assistant_message(turn_ctx: ChatContext) -> str | None:
#     """
#     Walk backwards through the chat context and return the text of the
#     most recent assistant message, or None if there isn't one yet.
#     Defensive about attribute names in case your installed livekit-agents
#     version structures ChatContext.items slightly differently.
#     """
#     items = getattr(turn_ctx, "items", None) or []
#     for item in reversed(items):
#         role = getattr(item, "role", None)
#         if role == "assistant":
#             return getattr(item, "text_content", None) or ""
#     return None

"""
agent.py

HR Voice Agent
LiveKit Agents v1.6.5
"""

import asyncio
import time

from livekit.agents import (
    Agent,
    RunContext,
    function_tool,
)
from livekit.agents.llm import ChatContext, ChatMessage, StopResponse

from services.employee_service import EmployeeService
from services.leave_service import LeaveService
from services.grievance_service import GrievanceService
from services.policy_service import PolicyService
from services.calendar_service import CalendarService
from memory.conversation_memory import ConversationMemory

from security.auth_service import AuthService
from security.auth_session import AuthSession
from config import SYSTEM_PROMPT
from modules.topic_guard import classify as _classify_topic

from tools.attendance_tools import AttendanceTools


# ============================================================================
# WATCHDOG TUNABLES
# ============================================================================
IDLE_WARNING_SECONDS = 60   # nudge the employee if this quiet
IDLE_TIMEOUT_SECONDS = 90   # end the call if still quiet after this
IDLE_POLL_SECONDS = 10       # how often the watchdog checks

LLM_FAILURE_LIMIT = 3        # consecutive LLM failures before ending the call


class HRAgent(Agent):

    def __init__(self):

        super().__init__(
            instructions=SYSTEM_PROMPT
        )

        self.memory = ConversationMemory()

        self.auth_service = AuthService()
        self.auth = AuthSession()

        self.employee_service = EmployeeService()
        self.leave_service = LeaveService()
        self.grievance_service = GrievanceService()
        self.policy_service = PolicyService()
        self.calendar_service = CalendarService()

        self.attendance_tools = AttendanceTools()

        # ── NEW: set by main.py's entrypoint right after HRAgent() is
        # constructed. Gives the agent a way to end its own call (idle
        # timeout / repeated LLM failure) via the same room-disconnect
        # path a normal hangup uses, so the existing FSM
        # (SESSION_ACTIVE -> ENDING -> READY) fires unchanged.
        self.job_ctx = None

        # ── NEW: idle watchdog state ────────────────────────────────────
        self._last_activity: float = time.time()
        self._idle_task: asyncio.Task | None = None
        self._warned_idle = False
        self._call_ending = False  # guards against double-ending

        # ── NEW: consecutive LLM failure tracking ───────────────────────
        self._llm_failure_count = 0

    async def on_enter(self):
        """Greet the user when the session starts, and start the idle watchdog.

        SIP callers (Plivo) need a brief settling delay (~1 s) after the
        SIP INVITE / 200 OK exchange before audio can flow cleanly.
        We detect SIP participants by checking for the 'sip:' identity
        prefix that LiveKit sets automatically on bridged participants.
        """
        self._last_activity = time.time()
        self._idle_task = asyncio.create_task(self._idle_watchdog())

        # ── Detect if this session arrived via a SIP trunk (e.g. Plivo) ──
        is_sip_call = False
        try:
            for participant in self.session.room.remote_participants.values():
                if participant.identity.startswith("sip_"):
                    is_sip_call = True
                    import logging
                    logging.getLogger(__name__).info(
                        "[HRAgent] SIP/Plivo caller detected: %s",
                        participant.identity,
                    )
                    break
        except Exception:
            pass  # room not yet fully populated — safe to ignore

        # Give SIP media a moment to stabilise before speaking
        if is_sip_call:
            await asyncio.sleep(1.0)

        await self.session.say(
            "Hello! I'm HR Buddy, your Agentic HR Voice Assistant. "
            "I can help you with leave requests, absence management, "
            "team coverage, HR policies, and more. "
            "How can I help you today?",
            allow_interruptions=True,
        )

    # ------------------------------------------------------------------
    # Code-enforced HR-only guardrail + activity tracking.
    #
    # Runs the instant the user finishes speaking, BEFORE the LLM is
    # invoked — so it can't be skipped by a model that decides not to
    # call check_topic(). If the message is off-topic, we speak the
    # refusal directly and raise StopResponse() so the LLM never runs
    # for this turn at all. Otherwise we return and the normal flow
    # continues exactly as before.
    # ------------------------------------------------------------------
    async def on_user_turn_completed(
        self,
        turn_ctx: ChatContext,
        new_message: ChatMessage,
    ) -> None:
        # NEW: any real user turn resets the idle clock, regardless of
        # whether it ends up HR-relevant or not — the employee IS present.
        self._last_activity = time.time()
        self._warned_idle = False

        text = (new_message.text_content or "").strip()
        if not text:
            return

        # If the assistant's last message was a follow-up question
        # (employee ID, confirmation, dates, OTP, etc.), treat this reply
        # as an in-context continuation and skip the guard. Without this,
        # plain answers like "one zero zero one", "yes", or spoken dates
        # get wrongly rejected because they contain no HR keyword on
        # their own — even though they're clearly part of an HR task
        # already in progress.
        last_assistant_text = _last_assistant_message(turn_ctx)
        if last_assistant_text and last_assistant_text.rstrip().endswith("?"):
            return  # let the LLM handle it normally, no guard check needed

        result = _classify_topic(text)

        if not result["allowed"]:
            await self.session.say(result["message"], allow_interruptions=True)
            raise StopResponse()

    # ------------------------------------------------------------------
    # NEW: idle watchdog — ends the call if the employee goes quiet.
    # ------------------------------------------------------------------
    async def _idle_watchdog(self):
        try:
            while True:
                await asyncio.sleep(IDLE_POLL_SECONDS)

                if self._call_ending:
                    return

                idle_for = time.time() - self._last_activity

                if idle_for > IDLE_WARNING_SECONDS and not self._warned_idle:
                    self._warned_idle = True
                    await self.session.say(
                        "Are you still there? I'll end this session shortly "
                        "if I don't hear from you.",
                        allow_interruptions=True,
                    )

                if idle_for > IDLE_TIMEOUT_SECONDS:
                    await self.session.say(
                        "I haven't heard from you in a while, so I'll end "
                        "this session now. Feel free to reach out again "
                        "anytime.",
                        allow_interruptions=False,
                    )
                    await self._end_call()
                    return
        except asyncio.CancelledError:
            # Normal shutdown path (employee hung up, call ended cleanly) —
            # nothing to clean up beyond letting the task exit.
            raise

    # ------------------------------------------------------------------
    # NEW: LLM-failure watchdog hooks.
    #
    # Wire _on_llm_success() into whatever marks a turn as having
    # completed cleanly, and _on_llm_failure() into wherever your LLM
    # call raises (e.g. OllamaLLMStream._run()'s except blocks in
    # ollama_llm.py, or an AgentSession error event if your installed
    # livekit-agents version exposes one). Without a real hook, this
    # logic simply never triggers — it is not self-wiring.
    # ------------------------------------------------------------------
    def on_llm_success(self):
        self._llm_failure_count = 0

    async def on_llm_failure(self):
        self._llm_failure_count += 1

        if self._llm_failure_count >= LLM_FAILURE_LIMIT:
            await self.session.say(
                "I'm having trouble processing requests right now. "
                "I'll end this session — please try again in a few minutes.",
                allow_interruptions=False,
            )
            await self._end_call()
        else:
            await self.session.say(
                "Sorry, I had trouble with that. Could you say it again?",
                allow_interruptions=True,
            )

    # ------------------------------------------------------------------
    # NEW: end the call through the same path a normal hangup uses.
    # ------------------------------------------------------------------
    async def _end_call(self):
        if self._call_ending:
            return
        self._call_ending = True

        if self._idle_task:
            self._idle_task.cancel()

        if self.job_ctx is not None and self.job_ctx.room is not None:
            # Routing through room.disconnect() means the existing
            # ctx.add_shutdown_callback(self.request_shutdown) in
            # voice_controller.py fires the same way it does for a
            # normal employee-initiated hangup, so SESSION_ACTIVE ->
            # ENDING -> READY happens unchanged.
            await self.job_ctx.room.disconnect()
        else:
            # Defensive fallback — should not normally happen if
            # main.py sets agent_instance.job_ctx before start_session().
            import logging
            logging.getLogger(__name__).warning(
                "[HRAgent] _end_call() invoked but job_ctx is not set; "
                "cannot disconnect the room programmatically."
            )

    async def on_exit(self):
        """Best-effort cleanup if the Agent base class calls this on teardown."""
        if self._idle_task:
            self._idle_task.cancel()

    ####################################################################
    # GUARDRAIL
    ####################################################################

    @function_tool()
    async def check_topic(
        self,
        context: RunContext,
        user_message: str,
    ):
        """
        MANDATORY GUARDRAIL — Call this tool FIRST whenever the user's request
        is ambiguous, seems unrelated to HR, or touches any of the following:

        - Weather, geography, science
        - Politics, current events, news
        - Finance, stocks, cryptocurrency
        - Sports, entertainment, movies, music
        - Recipes, food, restaurants
        - Travel, tourism, hotels
        - Programming, coding, software
        - Medical or legal advice
        - Jokes, stories, poems, creative writing
        - General knowledge or trivia

        This tool classifies the message and either:
          - Returns allowed=True  -> proceed normally
          - Returns allowed=False -> speak the 'message' field verbatim and stop

        DO NOT skip this tool for borderline requests.
        DO NOT answer non-HR questions even if you think you know the answer.
        """
        result = _classify_topic(user_message)
        return result

    ####################################################################
    # EMPLOYEE
    ####################################################################

    @function_tool()
    async def get_employee(
        self,
        context: RunContext,
        employee_id: int | None = None,
    ):
        """
        Retrieve complete information for an employee using their employee ID.

        Use this tool whenever the user asks for details about a specific employee
        and already provides the employee ID.

        Examples:
        - Show employee 1001.
        - Get details of employee ID 1045.
        - What is the information for employee 2005?

        Do NOT use this tool if the user only provides a name.
        Use search_employee instead.
        """
        if employee_id is None:
            return {"success": False, "message": "What's the employee ID?"}
        return self.employee_service.get_employee(employee_id)

    @function_tool()
    async def search_employee(
        self,
        context: RunContext,
        name: str,
    ):
        """
        Search employees by first name or last name.

        Use this tool whenever the user mentions an employee name instead of an ID.

        Examples:
        - Find John.
        - Search employee named Alice.
        - Do we have an employee called David?
        - Show me Ravi.

        Returns one or more matching employees.

        Do NOT use when an employee ID is available.
        """
        return self.employee_service.search_employee(name)

    @function_tool()
    async def employee_summary(
        self,
        context: RunContext,
        employee_id: int | None = None,
    ):
        """
        Retrieve a short summary of an employee.

        Includes information such as:
        - Name
        - Department
        - Designation
        - Manager
        - Contact information

        Use this when the user asks for an overview instead of complete employee details.

        Examples:
        - Give me a summary of employee 1005.
        - Tell me about employee 1005.
        """
        if employee_id is None:
            return {"success": False, "message": "What's the employee ID?"}
        return self.employee_service.get_employee_summary(employee_id)

    @function_tool()
    async def list_employees(
        self,
        context: RunContext,
    ):
        """
        Retrieve a list of all employees.

        Use only when the user explicitly asks to list employees.

        Examples:
        - List all employees.
        - Show every employee.
        - Display employee directory.
        """
        return self.employee_service.list_employees()

    ####################################################################
    # LEAVE
    ####################################################################

    @function_tool()
    async def leave_balance(
        self,
        context: RunContext,
        employee_id: int | None = None,
    ):
        """
        Retrieve the available leave balance for an employee.
        Requires the employee to be verified first.

        Examples:
        - How many casual leaves do I have?
        - What's my leave balance?
        - Remaining annual leave?
        - Sick leave left?
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first. What's your employee ID?"
            }
        # Use the verified identity — never trust an ID the model supplies
        # separately, since that's exactly how a stranger's balance could
        # get returned without anyone confirming who they actually are.
        employee_id = self.auth.employee_id
        return self.leave_service.check_leave_balance(employee_id)

    @function_tool()
    async def apply_leave(
        self,
        context: RunContext,
        from_date: str,
        to_date: str,
        leave_type: str,
    ):
        """
        Submit a leave or absence request for the authenticated employee.

        Delegates to the full absence pipeline which:
          - Checks team coverage threshold
          - Detects exception scenarios and routes to Area Manager
          - Syncs to IFS Cloud and SQL Server

        leave_type / absence_type must be one of:
          'Sickness', 'Holiday', 'Emergency', 'Funeral',
          'Compassionate', 'Casual', 'Earned'

        Always ask for confirmation before calling this tool.

        Examples:
        - I want leave tomorrow.
        - Apply casual leave.
        - Book annual leave.
        - I need holiday from Monday to Friday.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first. What is your employee ID?"
            }
        # Delegate to the full absence pipeline (coverage check, exception
        # detection, IFS/SQL sync) rather than the legacy LeaveService path.
        return self.attendance_tools.submit_absence_request(
            employee_id  = self.auth.employee_id,
            absence_type = leave_type,
            from_date    = from_date,
            to_date      = to_date,
            reason       = "",
        )

    @function_tool()
    async def cancel_leave(
        self,
        context: RunContext,
        request_id: int,
    ):
        """
        Cancel an existing leave request.

        Use only after the employee provides the leave request ID.

        Examples:
        - Cancel my leave.
        - Withdraw leave request 25.
        - Delete my leave application.

        Requires authentication.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first by providing your employee ID."
            }
        return self.leave_service.cancel_leave(request_id)

    @function_tool()
    async def leave_history(
        self,
        context: RunContext,
        employee_id: int | None = None,
    ):
        """
        Retrieve the leave history for the authenticated employee.

        Examples:
        - Show my leave history.
        - Previous leave applications.
        - What leave have I taken this year?
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first. What's your employee ID?"
            }
        employee_id = self.auth.employee_id
        return self.leave_service.leave_history(employee_id)

    ####################################################################
    # GRIEVANCE
    ####################################################################

    @function_tool()
    async def raise_grievance(
        self,
        context: RunContext,
        category: str,
        description: str,
        anonymous: bool = False,
    ):
        """
        Create a new grievance for the authenticated employee.

        Use only after collecting:
        - Category
        - Description
        - Anonymous or not

        Always ask for confirmation before submitting.

        Examples:
        - I want to report harassment.
        - Raise a grievance.
        - Report a workplace issue.
        - File a complaint.

        Requires authentication.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first by providing your employee ID."
            }
        return self.grievance_service.create_grievance(
            self.auth.employee_id,
            category,
            description,
            anonymous,
        )

    @function_tool()
    async def grievance_status(
        self,
        context: RunContext,
        grievance_id: int | None = None,
    ):
        """
        Retrieve the status of a grievance using its grievance ID.

        Examples:
        - Check grievance 45.
        - What's the status of my complaint?
        - Has grievance 20 been resolved?
        """
        if grievance_id is None:
            return {"success": False, "message": "What's the grievance ID?"}
        return self.grievance_service.get_grievance(grievance_id)

    @function_tool()
    async def employee_grievances(
        self,
        context: RunContext,
        employee_id: int | None = None,
    ):
        """
        Retrieve all grievances submitted by the authenticated employee.

        Examples:
        - Show my grievances.
        - List my complaints.
        - My grievance history.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first. What's your employee ID?"
            }
        employee_id = self.auth.employee_id
        return self.grievance_service.list_employee_grievances(employee_id)

    @function_tool()
    async def escalate_grievance(
        self,
        context: RunContext,
        grievance_id: int,
    ):
        """
        Escalate an existing grievance.

        Use only after confirming with the employee.

        Examples:
        - Escalate grievance 24.
        - I want HR management to review my complaint.

        Requires authentication.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first by providing your employee ID."
            }
        return self.grievance_service.escalate_grievance(grievance_id)

    ####################################################################
    # POLICY
    ####################################################################

    @function_tool()
    async def get_policy(
        self,
        context: RunContext,
        policy_name: str,
    ):
        """
        Retrieve the full HR policy for a specific topic.

        Use when the user asks about one known policy.

        Examples:
        - Explain maternity leave policy.
        - What is the work from home policy?
        - Show me the attendance policy.
        - Explain casual leave policy.

        Never invent policy information.
        Always use this tool.
        """
        return self.policy_service.get_policy(policy_name)

    @function_tool()
    async def search_policy(
        self,
        context: RunContext,
        keyword: str,
    ):
        """
        Search HR policies using keywords.

        Use when the user is unsure of the exact policy name.

        Examples:
        - Policies related to leave.
        - Travel policies.
        - Remote work.
        - Insurance.
        - Performance review.

        Return matching policies.
        """
        return self.policy_service.search_policy(keyword)

    @function_tool()
    async def leave_eligibility(
        self,
        context: RunContext,
        leave_type: str,
        years_of_service: int,
    ):
        """
        Check whether an employee is eligible for a specific leave type.

        Requires:
        - Leave type
        - Years of service

        Examples:
        - Am I eligible for maternity leave?
        - Can a new employee take earned leave?
        - Eligibility for paternity leave.
        """
        return self.policy_service.check_leave_eligibility(
            leave_type,
            years_of_service,
        )

    @function_tool()
    async def list_policies(
        self,
        context: RunContext,
    ):
        """
        Retrieve a list of all available HR policies.

        Use only when the employee asks to browse policies.

        Examples:
        - List all HR policies.
        - What policies are available?
        - Show company policies.
        """
        return self.policy_service.list_policies()

    ####################################################################
    # CALENDAR
    ####################################################################

    @function_tool()
    async def schedule_meeting(
        self,
        context: RunContext,
        title: str,
        event_date: str,
        event_time: str,
        duration: int = 30,
    ):
        """
        Schedule a meeting for the authenticated employee.

        Collect:
        - Meeting title
        - Date
        - Time
        - Duration

        Always confirm before scheduling.

        Examples:
        - Schedule a meeting tomorrow.
        - Book a meeting with HR.
        - Create an interview meeting.

        Requires authentication.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first by providing your employee ID."
            }
        return self.calendar_service.schedule_meeting(
            self.auth.employee_id,
            title,
            event_date,
            event_time,
            duration,
        )

    @function_tool()
    async def cancel_meeting(
        self,
        context: RunContext,
        event_id: int,
    ):
        """
        Cancel an existing meeting.

        Requires the meeting ID.

        Examples:
        - Cancel meeting 35.
        - Delete tomorrow's meeting.

        Requires authentication.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first by providing your employee ID."
            }
        return self.calendar_service.cancel_meeting(event_id)

    @function_tool()
    async def upcoming_meetings(
        self,
        context: RunContext,
        employee_id: int | None = None,
    ):
        """
        Retrieve all upcoming meetings for the authenticated employee.

        Examples:
        - My meetings.
        - What's on my calendar?
        - Upcoming meetings.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first. What's your employee ID?"
            }
        employee_id = self.auth.employee_id
        return self.calendar_service.upcoming_meetings(employee_id)

    @function_tool()
    async def available_slots(
        self,
        context: RunContext,
        employee_id: int,
        event_date: str,
    ):
        """
        Retrieve available meeting time slots for a specific day.

        Requires:
        - Employee ID
        - Date

        Examples:
        - Free slots tomorrow.
        - When am I available on Monday?
        - Available meeting times.
        """
        return self.calendar_service.available_slots(
            employee_id,
            event_date,
        )

    ####################################################################
    # SECURITY
    ####################################################################

    @function_tool()
    async def verify_employee(
        self,
        context: RunContext,
        employee_id: int | None = None,
    ):
        """
        Begin employee identity verification.

        Use before any operation that modifies company data.

        Examples:
        - Apply leave.
        - Raise grievance.
        - Cancel leave.
        - Schedule meeting.
        - Update employee information.

        The employee provides an employee ID.
        This tool generates an OTP for verification.
        """
        # IMPORTANT: employee_id must default to None in the signature
        # above, or this check is unreachable — a bare `employee_id: int`
        # makes the argument required at the Pydantic validation layer,
        # which runs BEFORE this function body and raises a ToolError
        # the model then has no clean way to recover from.
        if employee_id is None:
            return {
                "success": False,
                "message": "What's your employee ID?",
            }

        result = self.auth_service.begin_verification(employee_id)

        if not result["success"]:
            return result

        self.auth.employee_id = employee_id

        # Defensive: begin_verification()'s actual return shape may not
        # match result["employee"]["name"]; .get() avoids a KeyError crash.
        self.auth.employee_name = (
            result.get("employee", {}).get("name")
            or result.get("employee_name")
            or result.get("name")
            or "Employee"
        )

        self.auth.verification_pending = True
        self.auth.otp_sent = True

        return {
            "success": True,
            "message":
                f"For demo purposes, your OTP is {result['otp']}. Please provide this OTP to continue."
        }

    @function_tool()
    async def verify_otp(
        self,
        context: RunContext,
        otp: str | None = None,
    ):
        """
        Verify the OTP previously sent to the employee.

        Use immediately after verify_employee.

        Examples:
        - My OTP is 453281.
        - Verify 884522.
        - The code is 111222.

        After successful verification, the employee is authenticated.
        """
        if not otp:
            return {
                "success": False,
                "message": "What's the OTP code?",
            }

        if self.auth.employee_id is None:
            return {
                "success": False,
                "message": "No employee verification is in progress."
            }

        ok = self.auth_service.verify_otp(
            self.auth.employee_id,
            otp,
        )

        if not ok["success"]:
            return {
                "success": False,
                "message": "Invalid OTP."
            }

        self.auth.login(
            self.auth.employee_id,
            self.auth.employee_name,
        )

        return {
            "success": True,
            "message":
                f"Welcome {self.auth.employee_name}. Your identity has been verified."
        }

    ####################################################################
    # ATTENDANCE MANAGEMENT
    ####################################################################

    @function_tool()
    async def submit_absence_request(
        self,
        context: RunContext,
        absence_type: str,
        from_date: str,
        to_date: str,
        reason: str = "",
    ):
        """
        Submit an absence request for the authenticated field engineer.

        REQUIRES AUTHENTICATION before calling.

        absence_type must be one of:
          'Sickness'  — calling in sick on the day
          'Holiday'   — planned annual / casual holiday
          'Emergency' — unexpected emergency (short notice)
          'Funeral'   — bereavement or funeral leave
          'Casual'    — casual day off
          'Earned'    — earned / annual leave

        from_date and to_date must be in YYYY-MM-DD format.

        The system will automatically:
          - Check team coverage (80% minimum threshold)
          - Detect if the request requires Area Manager approval
          - Route exceptions to the Area Manager
          - Update IFS Cloud and SQL Server after approval

        Examples:
          - I'm sick today, please log it.
          - I want to book holiday from 2026-08-05 to 2026-08-07.
          - Emergency leave for tomorrow.
          - My father passed away, I need funeral leave.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity first. What is your employee ID?"
            }
        return self.attendance_tools.submit_absence_request(
            employee_id  = self.auth.employee_id,
            absence_type = absence_type,
            from_date    = from_date,
            to_date      = to_date,
            reason       = reason,
        )

    @function_tool()
    async def check_team_coverage(
        self,
        context: RunContext,
        field_manager_id: int,
        coverage_date: str,
    ):
        """
        Check the attendance coverage percentage for a Field Manager's team on a given date.

        Returns:
          - Total engineers in the team
          - Number available (not on leave)
          - Number absent (on approved/pending leave)
          - Coverage percentage
          - Whether coverage is above the 80% minimum threshold
          - A list of each engineer's status

        Use when a Field Manager asks:
          - How many of my team are in today?
          - What is my team's coverage on Monday?
          - Can I approve another absence?
          - Are we below threshold for this week?

        coverage_date must be in YYYY-MM-DD format.
        """
        return self.attendance_tools.check_team_coverage(
            field_manager_id = field_manager_id,
            coverage_date    = coverage_date,
        )

    @function_tool()
    async def get_team_roster(
        self,
        context: RunContext,
        field_manager_id: int,
        roster_date: str,
    ):
        """
        Retrieve the full daily roster for a Field Manager's team.

        Shows each engineer's shift status:
          - Scheduled (available)
          - OnLeave (absence approved)
          - The type of leave if absent

        Use when a Field Manager asks:
          - Show me the roster for Monday.
          - Who is in tomorrow?
          - Which engineers are on leave this week?

        roster_date must be in YYYY-MM-DD format.
        """
        return self.attendance_tools.get_team_roster(
            field_manager_id = field_manager_id,
            roster_date      = roster_date,
        )

    @function_tool()
    async def list_pending_exceptions(
        self,
        context: RunContext,
        area_manager_id: int,
    ):
        """
        Retrieve all exception absence requests that are pending the Area Manager's decision.

        Returns each exception with:
          - Exception ID
          - Employee name and dates
          - Type of exception (e.g. FuneralLeave, CoverageThresholdBreach)
          - Reason for the exception

        Use when an Area Manager asks:
          - What exceptions are waiting for my approval?
          - Show me pending absence approvals.
          - Do I have any requests to review?
        """
        return self.attendance_tools.list_pending_exceptions(
            area_manager_id = area_manager_id,
        )

    @function_tool()
    async def approve_exception(
        self,
        context: RunContext,
        exception_id: int,
        decision: str,
        notes: str = "",
    ):
        """
        Area Manager approves or rejects an exception absence request.

        REQUIRES AUTHENTICATION.

        decision must be 'Approved' or 'Rejected'.
        notes: optional reason or comments.

        After the decision:
          - The employee is notified automatically.
          - If Approved: IFS Cloud and SQL Server are updated.
          - If Approved: Roster is automatically reallocated.
          - If Rejected: The employee's leave balance is restored.

        Examples:
          - Approve exception 2.
          - Reject exception 3 — insufficient notice given.
          - I approve the funeral leave request.
        """
        if not self.auth.authenticated:
            return {
                "success": False,
                "message": "Please verify your identity as an Area Manager first."
            }
        return self.attendance_tools.approve_exception(
            exception_id    = exception_id,
            area_manager_id = self.auth.employee_id,
            decision        = decision,
            notes           = notes,
        )

    @function_tool()
    async def get_all_teams_coverage(
        self,
        context: RunContext,
        target_date: str,
    ):
        """
        Get a coverage summary across ALL Field Manager teams for a given date.

        Useful for Area Managers and operations staff to spot teams at risk of
        falling below the attendance threshold.

        Returns for each team:
          - Field Manager name
          - Total engineers / available / absent
          - Coverage percentage
          - Whether they are below threshold

        Examples:
          - How are all teams covered today?
          - Which teams are at risk this week?
          - Give me the operations coverage overview for Monday.

        target_date must be in YYYY-MM-DD format.
        """
        return self.attendance_tools.get_all_teams_coverage(
            target_date = target_date,
        )

    @function_tool()
    async def get_exception_detail(
        self,
        context: RunContext,
        exception_id: int,
    ):
        """
        Retrieve full details of a specific exception absence request.

        Returns:
          - Employee name and dates
          - Exception type and reason
          - Area Manager assigned and current status
          - AM decision and notes (if decided)

        Use when an Area Manager or Field Manager asks:
          - Tell me about exception 3.
          - What is the detail of exception request 7?
          - Show me the exception for employee 1023.
        """
        return self.attendance_tools.get_exception_detail(
            exception_id = exception_id,
        )


def _last_assistant_message(turn_ctx: ChatContext) -> str | None:
    """
    Walk backwards through the chat context and return the text of the
    most recent assistant message, or None if there isn't one yet.
    Defensive about attribute names in case your installed livekit-agents
    version structures ChatContext.items slightly differently.
    """
    items = getattr(turn_ctx, "items", None) or []
    for item in reversed(items):
        role = getattr(item, "role", None)
        if role == "assistant":
            return getattr(item, "text_content", None) or ""
    return None