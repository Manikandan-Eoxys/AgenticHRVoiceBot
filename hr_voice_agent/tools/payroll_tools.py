"""
tools/payroll_tools.py

LiveKit Function Tools for Payroll & Salary:
1. Salary disbursement schedule (last working day of the month)
2. Basic salary and compensation breakdown
3. Deductions breakdown (PF, PT, TDS, unpaid leaves)
4. Payslip availability and access instructions
"""

from livekit.agents import function_tool, RunContext
from services.payroll_service import PayrollService
from tools.tool_helpers import require_verified, get_verified_id

_payroll_service = PayrollService()


@function_tool
async def execute_get_salary_credit_date() -> str:
    """Inform caller about company salary payment dates and payslip distribution schedule."""
    res = _payroll_service.get_salary_credit_schedule()
    return res["message"]


@function_tool
async def execute_get_basic_salary_info(employee_id: str, context: RunContext) -> str:
    """Look up the caller's current basic salary, HRA, allowances, and gross salary."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    res = _payroll_service.get_basic_salary_info(emp_id)
    return res["message"]


@function_tool
async def execute_get_salary_deductions_info(
    month_year: str = "",
    employee_id: str = "",
    context: RunContext = None,
    month: str = "",
) -> str:
    """Explain the breakdown of deductions (Provident Fund / EPF, Professional Tax, TDS / Income Tax,
    and any unpaid leave deductions) for a specific month (e.g. 'August 2026') or recent paycheck."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    target_month = month_year or month or None
    res = _payroll_service.get_salary_deductions_breakdown(emp_id, target_month)
    return res["message"]


@function_tool
async def execute_check_payslip_status(
    month_year: str = "",
    employee_id: str = "",
    context: RunContext = None,
    month: str = "",
) -> str:
    """Check if the caller's payslip for a specific month (e.g. 'August 2026') is available,
    and confirm where they can access/download it."""
    err = require_verified(context, employee_id)
    if err:
        return err

    emp_id = get_verified_id(context, employee_id)
    target_month = month_year or month or None
    res = _payroll_service.check_payslip_status(emp_id, target_month)
    return res["message"]


# ── Backward compatibility aliases ────────────────────────────────────────────
get_salary_credit_date = execute_get_salary_credit_date
get_basic_salary_info = execute_get_basic_salary_info
get_salary_deductions_info = execute_get_salary_deductions_info
check_payslip_status = execute_check_payslip_status
