-- ============================================================
-- HR Voicebot Database — DEMO build (v3)
-- Employee IDs: 1001–1015
-- Leave types tracked: CL, SL, Maternity, Paternity, Comp Off,
-- Bereavement, Short Leave (hourly/half-day), LWP/LOP
--
-- Changes in this version (per request):
--   - REMOVED hike_records entirely (no hike/appraisal data at all).
--   - REMOVED the old flat `insurance` table.
--   - ADDED `insurance_plans` (3 fixed plans) + `employee_insurance`
--     (per-employee applied/claimed status), with reference data for
--     10 employees, each holding 2 applied plans and 1 claimed plan.
--   - ADDED `leave_requests`, a ledger for leave applications taken
--     through the voice agent's check -> confirm -> email flow.
--     Confirming a request also increments the matching row in
--     leave_balances.used_days.
-- ============================================================

DROP DATABASE IF EXISTS hr_voicebot;
CREATE DATABASE hr_voicebot;
USE hr_voicebot;

-- ============================================================
-- 1. DEPARTMENTS & DESIGNATIONS
-- ============================================================
CREATE TABLE departments (
    department_id   INT AUTO_INCREMENT PRIMARY KEY,
    department_name VARCHAR(100) NOT NULL UNIQUE
);

INSERT INTO departments (department_name) VALUES
    ('Embedded Software'),
    ('Embedded Hardware'),
    ('IT Software'),
    ('IT Admin');

CREATE TABLE designations (
    designation_id   INT AUTO_INCREMENT PRIMARY KEY,
    designation_name VARCHAR(100) NOT NULL UNIQUE
);

INSERT INTO designations (designation_name) VALUES
    ('Embedded Software Engineer'),
    ('Senior Embedded Software Engineer'),
    ('Embedded Hardware Engineer'),
    ('IT Software Engineer'),
    ('Senior IT Software Engineer'),
    ('Lead Software Engineer'),
    ('IT Admin');

-- ============================================================
-- 2. EMPLOYEES  (IDs 1001–1015)
-- gender is stored only to drive Maternity/Paternity scheme &
-- leave-balance eligibility below — never spoken by the bot directly.
-- ============================================================
CREATE TABLE employees (
    employee_id    VARCHAR(10) PRIMARY KEY,
    full_name      VARCHAR(100) NOT NULL,
    gender         VARCHAR(10) NOT NULL CHECK (gender IN ('F','M')),
    email          VARCHAR(150) UNIQUE,
    department_id  INT NOT NULL REFERENCES departments(department_id),
    designation_id INT NOT NULL REFERENCES designations(designation_id),
    join_date      DATE NOT NULL,
    status         VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','on_leave','exited')),
    phone_number   VARCHAR(20),
    manager_id     VARCHAR(10) REFERENCES employees(employee_id)
);

INSERT INTO employees (employee_id, full_name, gender, email, department_id, designation_id, join_date, status, phone_number, manager_id) VALUES
('1001', 'Ravikala',    'F', 'ravikala@eoxys.com',    1, 1, '2023-03-31', 'active',   '+919500000001', '1007'),
('1002', 'Deepika',     'F', 'deepika@efabric.com',   1, 2, '2022-12-11', 'on_leave', '+919500000002', '1007'),
('1003', 'Narmatha',    'F', 'narmatha@eoxys.com',    1, 1, '2023-09-01', 'active',   '+919500000003', '1007'),
('1004', 'Hashir',      'M', 'hashir@efabric.com',    1, 1, '2024-02-21', 'active',   '+919500000004', '1007'),
('1005', 'Saikumar',    'M', 'saikumar@efabric.com',  1, 1, '2024-02-21', 'active',   '+919500000005', '1007'),
('1006', 'Bharath',     'M', 'bharath@efabric.com',   1, 1, '2024-02-21', 'active',   '+919500000006', '1007'),
('1007', 'Guna',        'M', 'guna@efabric.com',      1, 2, '2023-12-21', 'active',   '+917639878324', NULL),
('1008', 'Shivaraj',    'M', 'shivaraj@efabric.com',  1, 1, '2025-02-01', 'active',   '+919500000008', '1007'),
('1009', 'Govindaraj',  'M', 'govindaraj@efabric.com',1, 1, '2025-02-01', 'active',   '+919500000009', '1007'),
('1010', 'Charan',      'M', 'charan@efabric.com',    2, 3, '2024-07-01', 'active',   '+919500000010', '1007'),
('1011', 'Momin',       'M', 'momin@efabric.com',     2, 3, '2023-09-06', 'active',   '+919500000011', '1007'),
('1012', 'Satheesh',    'M', 'satheesh@efabric.com',  3, 4, '2023-04-07', 'active',   '+919500000012', '1014'),
('1013', 'Kamal',       'M', 'kamal@efabric.com',     1, 1, '2025-09-05', 'active',   '+919786586806', '1014'),
('1014', 'Manikandan',  'M', 'manikandan@eoxys.com',  1, 1, '2025-09-05', 'active',   '+918925355704', NULL),
('1015', 'Sujitsaju',   'M', 'sujitsaju@efabric.com', 3, 4, '2023-04-07', 'active',   '+919500000015', '1014');

-- ============================================================
-- 3. LEAVE POLICY (static reference text — 8 leave types)
-- ============================================================
CREATE TABLE leave_policy (
    leave_code     VARCHAR(20) PRIMARY KEY,
    leave_name     VARCHAR(60) NOT NULL,
    annual_range   VARCHAR(40) NOT NULL,
    short_note     VARCHAR(150) NOT NULL,
    description    TEXT NOT NULL,
    applies_to     VARCHAR(20) NOT NULL DEFAULT 'All' CHECK (applies_to IN ('All','Female','Male'))
);

INSERT INTO leave_policy (leave_code, leave_name, annual_range, short_note, description, applies_to) VALUES
('CL', 'Casual Leave', '7–12 days/yr',
    'Short, unplanned; usually can''t be clubbed with other leave; no carry-forward',
    'Casual Leave is for urgent personal reasons, usually 8–12 days annually.', 'All'),
('SL', 'Sick/Medical Leave', '10–12 days/yr',
    'Medical certificate needed beyond 2–3 days; usually not encashable',
    'Sick Leave is generally 10–12 days per year, requiring medical proof for extended periods.', 'All'),
('MATERNITY', 'Maternity Leave', '26 weeks (12 for 3rd+ child)',
    'Statutory, mandatory, non-negotiable',
    'Maternity Leave provides 26 weeks of paid leave, 12 weeks for women with two or more surviving children, and 12 weeks for adoption of a child under three months.', 'Female'),
('PATERNITY', 'Paternity Leave', '5–15 days (policy, not law)',
    'Not centrally mandated in the private sector, but common practice',
    'Paternity leave isn''t legally mandated in the private sector, but many organizations voluntarily grant 5–15 days as part of their HR policy.', 'Male'),
('COMP_OFF', 'Compensatory Off (Comp Off)', 'Per instance',
    'Given for working a holiday/weekly-off',
    'Compensatory Off is granted to employees who work on holidays or weekly offs, entitling them to an equivalent paid day off or overtime compensation.', 'All'),
('BEREAVEMENT', 'Bereavement Leave', '3–7 days',
    'Not mandated, but increasingly standard practice',
    'Bereavement Leave typically runs 3–7 days depending on how close the deceased family member was.', 'All'),
('SHORT_LEAVE', 'Short Leave (hourly/half-day)', 'N/A',
    'Partial-day absence, separate from full CL, still needs logging',
    'A short leave application is a formal request for a partial workday absence — typically a few hours — used when a full day off isn''t needed, and companies with an online system let employees apply for half-day or short leave directly, speeding up approvals and keeping attendance accurate.', 'All'),
('LWP', 'Leave Without Pay (LWP/LOP)', 'Fallback',
    'Used once other balances are exhausted; you already have this one',
    'Leave Without Pay is unpaid leave taken with company approval once paid leave options are exhausted, and it typically affects salary, benefits, and sometimes seniority.', 'All');

-- ============================================================
-- 4. LEAVE BALANCES
-- One row per employee per leave type THEY are eligible for.
-- ============================================================
CREATE TABLE leave_balances (
    employee_id    VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    leave_code     VARCHAR(20) NOT NULL REFERENCES leave_policy(leave_code),
    entitled_days  INT NULL,                -- NULL = no fixed cap (e.g. LWP)
    used_days      INT NOT NULL DEFAULT 0,
    available_days INT GENERATED ALWAYS AS (
        CASE WHEN entitled_days IS NULL THEN NULL ELSE entitled_days - used_days END
    ) STORED,
    PRIMARY KEY (employee_id, leave_code)
);

INSERT INTO leave_balances (employee_id, leave_code, entitled_days, used_days) VALUES
-- 1001 Ravikala (F)
('1001','CL',10,4), ('1001','SL',11,7), ('1001','MATERNITY',182,0), ('1001','COMP_OFF',2,0), ('1001','BEREAVEMENT',5,0), ('1001','SHORT_LEAVE',12,3), ('1001','LWP',NULL,0),
-- 1002 Narmatha (F) — currently on_leave, mid-way through maternity leave
('1002','CL',10,6), ('1002','SL',11,5), ('1002','MATERNITY',182,60), ('1002','COMP_OFF',1,1), ('1002','BEREAVEMENT',5,0), ('1002','SHORT_LEAVE',12,1), ('1002','LWP',NULL,0),
-- 1003 Deepika (F)
('1003','CL',10,3), ('1003','SL',11,1), ('1003','MATERNITY',182,0), ('1003','COMP_OFF',3,2), ('1003','BEREAVEMENT',5,2), ('1003','SHORT_LEAVE',12,4), ('1003','LWP',NULL,1),
-- 1004 Bharath (M)
('1004','CL',10,5), ('1004','SL',11,3), ('1004','PATERNITY',15,0), ('1004','COMP_OFF',2,1), ('1004','BEREAVEMENT',5,0), ('1004','SHORT_LEAVE',12,2), ('1004','LWP',NULL,0),
-- 1005 Saikumar (M) — recently availed full paternity leave
('1005','CL',10,7), ('1005','SL',11,4), ('1005','PATERNITY',15,15), ('1005','COMP_OFF',1,0), ('1005','BEREAVEMENT',5,0), ('1005','SHORT_LEAVE',12,1), ('1005','LWP',NULL,2),
-- 1006 Charan (M)
('1006','CL',10,6), ('1006','SL',11,6), ('1006','PATERNITY',15,0), ('1006','COMP_OFF',0,0), ('1006','BEREAVEMENT',5,0), ('1006','SHORT_LEAVE',12,0), ('1006','LWP',NULL,0),
-- 1007 Dharmaraj (M)
('1007','CL',10,8), ('1007','SL',11,7), ('1007','PATERNITY',15,5), ('1007','COMP_OFF',4,3), ('1007','BEREAVEMENT',5,3), ('1007','SHORT_LEAVE',12,5), ('1007','LWP',NULL,3),
-- 1008 Shivaraj (M)
('1008','CL',10,2), ('1008','SL',11,1), ('1008','PATERNITY',15,0), ('1008','COMP_OFF',1,0), ('1008','BEREAVEMENT',5,0), ('1008','SHORT_LEAVE',12,1), ('1008','LWP',NULL,0),
-- 1009 Govindaraj (M)
('1009','CL',10,4), ('1009','SL',11,2), ('1009','PATERNITY',15,0), ('1009','COMP_OFF',2,1), ('1009','BEREAVEMENT',5,0), ('1009','SHORT_LEAVE',12,2), ('1009','LWP',NULL,0),
-- 1010 Hashir (M)
('1010','CL',10,9), ('1010','SL',11,8), ('1010','PATERNITY',15,0), ('1010','COMP_OFF',0,0), ('1010','BEREAVEMENT',5,0), ('1010','SHORT_LEAVE',12,6), ('1010','LWP',NULL,4),
-- 1011 Momin (M)
('1011','CL',10,5), ('1011','SL',11,5), ('1011','PATERNITY',15,0), ('1011','COMP_OFF',1,1), ('1011','BEREAVEMENT',5,0), ('1011','SHORT_LEAVE',12,3), ('1011','LWP',NULL,0),
-- 1012 Priya (F) — not enrolled in Maternity scheme, no rows for it
('1012','CL',10,3), ('1012','SL',11,2), ('1012','COMP_OFF',0,0), ('1012','BEREAVEMENT',5,0), ('1012','SHORT_LEAVE',12,2), ('1012','LWP',NULL,0),
-- 1013 Kamal (M)
('1013','CL',10,1), ('1013','SL',11,0), ('1013','PATERNITY',15,0), ('1013','COMP_OFF',0,0), ('1013','BEREAVEMENT',5,0), ('1013','SHORT_LEAVE',12,0), ('1013','LWP',NULL,0),
-- 1014 Manikandan (M)
('1014','CL',10,2), ('1014','SL',11,1), ('1014','PATERNITY',15,0), ('1014','COMP_OFF',1,0), ('1014','BEREAVEMENT',5,0), ('1014','SHORT_LEAVE',12,1), ('1014','LWP',NULL,0),
-- 1015 Sujitsaju (M)
('1015','CL',10,6), ('1015','SL',11,6), ('1015','PATERNITY',15,3), ('1015','COMP_OFF',2,2), ('1015','BEREAVEMENT',5,1), ('1015','SHORT_LEAVE',12,4), ('1015','LWP',NULL,1);

-- ============================================================
-- 5. EMPLOYEE SCHEMES
-- EPF for everyone; Maternity Benefit ONLY for Ravikala, Narmatha,
-- Deepika; Paternity Benefit for the men.
-- ============================================================
CREATE TABLE schemes (
    scheme_id    INT AUTO_INCREMENT PRIMARY KEY,
    scheme_name  VARCHAR(100) NOT NULL,
    category     VARCHAR(20) NOT NULL CHECK (category IN ('Statutory','Voluntary'))
);

INSERT INTO schemes (scheme_name, category) VALUES
('Employees'' Provident Fund (EPF)', 'Statutory'),
('Maternity Benefit', 'Statutory'),
('Paternity Benefit', 'Voluntary');

CREATE TABLE employee_schemes (
    employee_id   VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    scheme_id     INT NOT NULL REFERENCES schemes(scheme_id),
    enrolled_date DATE NOT NULL,
    PRIMARY KEY (employee_id, scheme_id)
);

-- EPF: everyone
INSERT INTO employee_schemes (employee_id, scheme_id, enrolled_date)
SELECT employee_id, 1, join_date FROM employees;

-- Maternity Benefit: only Ravikala (1001), Narmatha (1002), Deepika (1003)
INSERT INTO employee_schemes (employee_id, scheme_id, enrolled_date) VALUES
('1001', 2, '2023-03-31'),
('1002', 2, '2022-12-11'),
('1003', 2, '2023-09-01');

-- Paternity Benefit: all men
INSERT INTO employee_schemes (employee_id, scheme_id, enrolled_date)
SELECT employee_id, 3, join_date FROM employees WHERE gender = 'M';

-- ============================================================
-- 6. INSURANCE — 3 fixed company plans + per-employee applied/claimed
-- status. Reference data covers 10 employees (1001–1010); each holds
-- exactly 2 applied plans, one of which has also been claimed.
-- ============================================================
CREATE TABLE insurance_plans (
    plan_code    VARCHAR(20) PRIMARY KEY,
    plan_name    VARCHAR(100) NOT NULL,
    plan_type    VARCHAR(50) NOT NULL,
    sum_insured  DECIMAL(10,2) NOT NULL,
    description  VARCHAR(255) NOT NULL
);

INSERT INTO insurance_plans (plan_code, plan_name, plan_type, sum_insured, description) VALUES
('IND_HEALTH', 'Individual Health Plan',   'Health',     300000.00, 'Individual health coverage up to Rs.3,00,000'),
('ACCIDENT',   'Accidental Coverage',      'Accident',   500000.00, 'Accidental coverage up to Rs.5,00,000'),
('SENIOR_CIT', 'Senior Citizen Insurance', 'Senior Care',100000.00, 'Senior citizen (dependent parent) coverage up to Rs.1,00,000');

CREATE TABLE employee_insurance (
    employee_id    VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    plan_code      VARCHAR(20) NOT NULL REFERENCES insurance_plans(plan_code),
    applied_date   DATE NOT NULL,
    claimed        BOOLEAN NOT NULL DEFAULT FALSE,
    claimed_date   DATE NULL,
    PRIMARY KEY (employee_id, plan_code)
);

-- Each of the 10 reference employees applied for 2 of the 3 plans,
-- and has claimed exactly 1 of those 2.
INSERT INTO employee_insurance (employee_id, plan_code, applied_date, claimed, claimed_date) VALUES
('1001', 'IND_HEALTH', '2023-04-15', TRUE,  '2024-01-10'),
('1001', 'ACCIDENT',   '2023-04-15', FALSE, NULL),
('1002', 'IND_HEALTH', '2023-01-05', FALSE, NULL),
('1002', 'SENIOR_CIT', '2023-01-05', TRUE,  '2024-06-20'),
('1003', 'ACCIDENT',   '2023-09-20', TRUE,  '2024-03-11'),
('1003', 'SENIOR_CIT', '2023-09-20', FALSE, NULL),
('1004', 'IND_HEALTH', '2024-03-01', FALSE, NULL),
('1004', 'ACCIDENT',   '2024-03-01', TRUE,  '2024-11-02'),
('1005', 'IND_HEALTH', '2024-03-01', TRUE,  '2025-02-14'),
('1005', 'SENIOR_CIT', '2024-03-01', FALSE, NULL),
('1006', 'ACCIDENT',   '2024-03-05', FALSE, NULL),
('1006', 'SENIOR_CIT', '2024-03-05', TRUE,  '2025-01-18'),
('1007', 'IND_HEALTH', '2024-01-10', TRUE,  '2024-09-09'),
('1007', 'ACCIDENT',   '2024-01-10', FALSE, NULL),
('1008', 'IND_HEALTH', '2025-02-15', FALSE, NULL),
('1008', 'SENIOR_CIT', '2025-02-15', TRUE,  '2025-07-01'),
('1009', 'ACCIDENT',   '2025-02-15', TRUE,  '2025-06-19'),
('1009', 'SENIOR_CIT', '2025-02-15', FALSE, NULL),
('1010', 'IND_HEALTH', '2024-07-15', FALSE, NULL),
('1010', 'ACCIDENT',   '2024-07-15', TRUE,  '2025-03-22');

-- ============================================================
-- 7. CORE HR POLICIES (5 Official Policies)
-- 1. Leave & Holiday Policy
-- 2. Attendance & Regularization Policy
-- 3. Work From Home / Hybrid Work Policy
-- 4. Payroll & Salary Policy
-- 5. Code of Conduct & Grievance Policy
-- ============================================================
CREATE TABLE hr_policies (
    policy_code     VARCHAR(30) PRIMARY KEY,
    policy_name     VARCHAR(100) NOT NULL,
    category        VARCHAR(50) NOT NULL,
    summary         TEXT NOT NULL,
    details         TEXT NOT NULL
);

INSERT INTO hr_policies (policy_code, policy_name, category, summary, details) VALUES
('LEAVE_HOLIDAY', 'Leave & Holiday Policy', 'Leave Management',
 'Covers annual leave entitlements (CL, SL, Maternity, Paternity, Comp Off, Bereavement, Short Leave, LWP), carry-forward rules, and public holidays.',
 'Casual Leave (CL): 10-12 days per year for urgent personal matters, no carry-forward to the next calendar year. Sick Leave (SL): 10-12 days per year; medical certificate required for absence exceeding 2 consecutive days. Maternity Leave: 26 weeks statutory paid leave for eligible female employees. Paternity Leave: 15 days for male employees. Comp Off: 1 day granted for working on a declared holiday or weekend with prior approval. Short Leave: up to 2 hours or half-day for partial workday emergencies. Earned leaves can be carried forward up to a maximum of 30 days. All declared public holidays are paid days off.'),

('ATTENDANCE_REG', 'Attendance & Regularization Policy', 'Attendance',
 'Governs daily working hours (9:00 AM - 6:00 PM), grace periods, late mark penalties, and attendance regularization requests.',
 'Standard office working hours are 9:00 AM to 6:00 PM, Monday through Friday (8 working hours plus 1 hour lunch/break). A 15-minute grace period applies in the morning, meaning check-ins up to 9:15 AM are not marked late. Arrival between 9:16 AM and 10:00 AM is logged as a late mark. Accumulating 3 late marks in a single month results in a half-day salary or leave deduction. If an employee forgets to punch in or punch out, or biometric fails, they can submit an Attendance Regularization request within 2 business days. Maximum 3 regularizations allowed per calendar month with manager approval.'),

('WFH_HYBRID', 'Work From Home / Hybrid Work Policy', 'Workplace Flexibility',
 'Specifies hybrid work eligibility, monthly WFH quotas, core active hours, and manager approval requirements.',
 'Full-time confirmed employees who have completed their probation period are eligible for hybrid work. Eligible employees may work from home up to 2 days per week or up to 8 days per calendar month. WFH applications must be submitted at least 24 hours in advance and require manager approval. Employees working from home must be available during core hours (9:30 AM to 5:30 PM), be reachable via phone, email, and Slack/Teams, and submit an end-of-day summary.'),

('PAYROLL_SALARY', 'Payroll & Salary Policy', 'Compensation & Benefits',
 'Details salary payment schedules, statutory deductions (PF, PT, TDS), payslip distribution, and payroll discrepancy resolutions.',
 'Monthly salaries are disbursed directly to employee bank accounts on the last working day of every calendar month. Statutory deductions include Provident Fund (EPF at 12% of basic wage), Professional Tax (PT, Rs.200), and Tax Deducted at Source (TDS based on income tax slab). Unpaid leave (LWP) is deducted at a daily rate based on gross salary. Monthly payslips are generated and made available on the 1st of every month via the HR portal and emailed to registered email addresses. Employees may raise a payroll ticket for any salary discrepancies or deduction inquiries.'),

('CONDUCT_GRIEVANCE', 'Code of Conduct & Grievance Policy', 'Workplace Ethics & Relations',
 'Defines workplace code of conduct, dress code, company asset rules, conflict of interest, and confidential grievance escalation.',
 'Dress code: Business casuals Monday through Thursday, smart casuals on Fridays. Company laptops and digital assets are for authorized company business only; VPN usage is mandatory when accessing company systems remotely. Conflicts of interest and external employment are strictly prohibited without written approval. The company maintains zero tolerance for harassment, discrimination, or abusive behavior. Sensitive complaints (harassment, manager disputes, misconduct) are escalated confidentially to senior HR and the Internal Complaints Committee (ICC) for investigation within 3 to 5 business days.');

-- ============================================================
-- 8. COMPANY HOLIDAYS (2026 Calendar)
-- ============================================================
CREATE TABLE company_holidays (
    holiday_id      INT AUTO_INCREMENT PRIMARY KEY,
    holiday_name    VARCHAR(100) NOT NULL,
    holiday_date    DATE NOT NULL UNIQUE,
    holiday_day     VARCHAR(20) NOT NULL,
    is_mandatory    BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO company_holidays (holiday_name, holiday_date, holiday_day, is_mandatory) VALUES
('New Year''s Day',                   '2026-01-01', 'Thursday',  TRUE),
('Republic Day',                     '2026-01-26', 'Monday',    TRUE),
('Maha Shivratri',                  '2026-03-03', 'Tuesday',   TRUE),
('Eid-ul-Fitr',                     '2026-03-20', 'Friday',    TRUE),
('Good Friday',                      '2026-04-03', 'Friday',    TRUE),
('Tamil New Year / Ambedkar Jayanti', '2026-04-14', 'Tuesday',   TRUE),
('May Day / Labour Day',             '2026-05-01', 'Friday',    TRUE),
('Independence Day',                 '2026-08-15', 'Saturday',  TRUE),
('Gandhi Jayanti',                   '2026-10-02', 'Friday',    TRUE),
('Ayudha Pooja / Dussehra',         '2026-10-20', 'Tuesday',   TRUE),
('Diwali / Deepavali',               '2026-11-08', 'Sunday',    TRUE),
('Christmas',                        '2026-12-25', 'Friday',    TRUE);

-- ============================================================
-- 9. ATTENDANCE RECORDS (Recent Biometric & Status Log)
-- ============================================================
CREATE TABLE attendance_records (
    record_id       INT AUTO_INCREMENT PRIMARY KEY,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    record_date     DATE NOT NULL,
    check_in        TIME NULL,
    check_out       TIME NULL,
    total_hours     DECIMAL(4,2) NULL,
    status          VARCHAR(20) NOT NULL CHECK (status IN ('Present', 'Absent', 'Late', 'Half_Day', 'Holiday', 'On_Leave')),
    late_minutes    INT NOT NULL DEFAULT 0,
    remarks         VARCHAR(255) NULL,
    UNIQUE KEY (employee_id, record_date)
);

INSERT INTO attendance_records (employee_id, record_date, check_in, check_out, total_hours, status, late_minutes, remarks) VALUES
('1001', '2026-09-17', NULL, NULL, NULL, 'Absent', 0, 'No biometric punch recorded'),
('1001', '2026-09-16', '09:35:00', '18:15:00', 8.67, 'Late', 20, 'Arrived 20 mins past grace period'),
('1001', '2026-09-15', '08:55:00', '18:05:00', 9.17, 'Present', 0, 'On time'),
('1001', '2026-09-14', '09:05:00', '18:00:00', 8.92, 'Present', 0, 'On time within grace period'),
('1001', '2026-09-08', '09:28:00', '18:10:00', 8.70, 'Late', 13, 'Late arrival (traffic)'),
('1001', '2026-09-02', '09:40:00', '18:25:00', 8.75, 'Late', 25, 'Late arrival'),
('1002', '2026-09-17', NULL, NULL, NULL, 'On_Leave', 0, 'Maternity leave'),
('1014', '2026-09-17', '08:50:00', '18:30:00', 9.67, 'Present', 0, 'On time'),
('1014', '2026-09-16', '09:00:00', '18:00:00', 9.00, 'Present', 0, 'On time');

-- ============================================================
-- 10. ATTENDANCE REGULARIZATIONS
-- ============================================================
CREATE TABLE attendance_regularizations (
    regularization_id INT AUTO_INCREMENT PRIMARY KEY,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    attendance_date DATE NOT NULL,
    request_type    VARCHAR(50) NOT NULL CHECK (request_type IN ('missing_punch_in', 'missing_punch_out', 'late_regularization', 'status_correction')),
    actual_time     TIME NULL,
    reason          VARCHAR(255) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    manager_id      VARCHAR(10) NULL,
    manager_remarks VARCHAR(255) NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO attendance_regularizations (employee_id, attendance_date, request_type, actual_time, reason, status) VALUES
('1001', '2026-09-01', 'missing_punch_in', '09:05:00', 'Fingerprint scanner not detected', 'approved');

-- ============================================================
-- 11. WORK FROM HOME (WFH) REQUESTS
-- ============================================================
CREATE TABLE wfh_requests (
    wfh_id          INT AUTO_INCREMENT PRIMARY KEY,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    days_count      INT NOT NULL DEFAULT 1,
    reason          VARCHAR(255) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    manager_id      VARCHAR(10) NULL,
    manager_remarks VARCHAR(255) NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO wfh_requests (employee_id, start_date, end_date, days_count, reason, status) VALUES
('1001', '2026-09-04', '2026-09-05', 2, 'Home electrical maintenance', 'approved'),
('1001', '2026-09-25', '2026-09-25', 1, 'Delivery and family visit', 'pending');

-- ============================================================
-- 12. PAYROLL RECORDS
-- ============================================================
CREATE TABLE payroll_records (
    payroll_id       INT AUTO_INCREMENT PRIMARY KEY,
    employee_id      VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    month_year       VARCHAR(20) NOT NULL,
    basic_salary     DECIMAL(10,2) NOT NULL,
    hra              DECIMAL(10,2) NOT NULL,
    allowances       DECIMAL(10,2) NOT NULL,
    gross_salary     DECIMAL(10,2) NOT NULL,
    deductions_pf    DECIMAL(10,2) NOT NULL,
    deductions_pt    DECIMAL(10,2) NOT NULL,
    deductions_tax   DECIMAL(10,2) NOT NULL,
    deductions_other DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    net_salary       DECIMAL(10,2) NOT NULL,
    payment_status   VARCHAR(20) NOT NULL DEFAULT 'Credited' CHECK (payment_status IN ('Credited', 'Pending', 'Processing')),
    payment_date     DATE NULL,
    payslip_available BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE KEY (employee_id, month_year)
);

INSERT INTO payroll_records (employee_id, month_year, basic_salary, hra, allowances, gross_salary, deductions_pf, deductions_pt, deductions_tax, deductions_other, net_salary, payment_status, payment_date, payslip_available) VALUES
('1001', 'August 2026',    35000.00, 15000.00, 10000.00, 60000.00, 4200.00, 200.00, 2500.00, 0.00, 53100.00, 'Credited', '2026-08-31', TRUE),
('1001', 'July 2026',      35000.00, 15000.00, 10000.00, 60000.00, 4200.00, 200.00, 2500.00, 0.00, 53100.00, 'Credited', '2026-07-31', TRUE),
('1001', 'September 2026', 35000.00, 15000.00, 10000.00, 60000.00, 4200.00, 200.00, 2500.00, 0.00, 53100.00, 'Credited', '2026-09-30', TRUE),
('1002', 'August 2026',    42000.00, 18000.00, 12000.00, 72000.00, 5040.00, 200.00, 3500.00, 0.00, 63260.00, 'Credited', '2026-08-31', TRUE),
('1014', 'August 2026',    55000.00, 22000.00, 15000.00, 92000.00, 6600.00, 200.00, 6000.00, 0.00, 79200.00, 'Credited', '2026-08-31', TRUE),
('1014', 'September 2026', 55000.00, 22000.00, 15000.00, 92000.00, 6600.00, 200.00, 6000.00, 0.00, 79200.00, 'Credited', '2026-09-30', TRUE);

-- ============================================================
-- 13. LEAVE REQUESTS & APPROVAL WORKFLOW
-- ============================================================
CREATE TABLE leave_requests (
    request_id       INT AUTO_INCREMENT PRIMARY KEY,
    employee_id      VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    leave_code       VARCHAR(20) NOT NULL REFERENCES leave_policy(leave_code),
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    days_requested   INT NOT NULL,
    reason           VARCHAR(255),
    status           VARCHAR(20) NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','approved','rejected','cancelled')),
    rejection_reason VARCHAR(255) NULL,
    manager_remarks  VARCHAR(255) NULL,
    reminder_sent_at TIMESTAMP NULL,
    notified_email   VARCHAR(150),
    requested_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO leave_requests (request_id, employee_id, leave_code, start_date, end_date, days_requested, reason, status, rejection_reason, manager_remarks, notified_email) VALUES
(101, '1001', 'CL', '2026-10-05', '2026-10-07', 3, 'Attending family wedding ceremony', 'approved', NULL, 'Approved. Please ensure handover before leaving.', 'manikandan.eoxys@gmail.com'),
(102, '1001', 'CL', '2026-08-14', '2026-08-14', 1, 'Long weekend travel', 'rejected', 'Team coverage is required on that date', 'Project delivery deadline on Friday.', 'manikandan.eoxys@gmail.com'),
(103, '1001', 'SL', '2026-09-28', '2026-09-29', 2, 'Doctor consultation and rest', 'submitted', NULL, NULL, 'manikandan.eoxys@gmail.com');

-- ============================================================
-- 14. HR TICKETS (Human Intervention / Escalation)
-- 6 Categories:
-- 1. Payroll & Salary Issues
-- 2. Attendance Issues
-- 3. Leave Issues
-- 4. HRMS / Employee Profile Issues
-- 5. HR Documents
-- 6. Work From Home / Hybrid Work
-- ============================================================
CREATE TABLE hr_tickets (
    ticket_id        INT AUTO_INCREMENT PRIMARY KEY,
    ticket_number    VARCHAR(30) UNIQUE NOT NULL,
    employee_id      VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    category         VARCHAR(60) NOT NULL,
    ticket_type      VARCHAR(60) NOT NULL,
    description      TEXT NOT NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'Open' CHECK (status IN ('Open', 'In_Progress', 'Resolved', 'Closed')),
    priority         VARCHAR(20) NOT NULL DEFAULT 'Normal' CHECK (priority IN ('Normal', 'Urgent', 'Confidential')),
    resolution_notes TEXT NULL,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT INTO hr_tickets (ticket_number, employee_id, category, ticket_type, description, status, priority) VALUES
('TICK-10021', '1001', 'Payroll & Salary Issues', 'PAYROLL_SALARY_DISCREPANCY', 'Salary query regarding August TDS deduction breakdown', 'In_Progress', 'Normal'),
('TICK-10022', '1001', 'HR Documents', 'EMPLOYMENT_CERTIFICATE_REQUEST', 'Request for employment certificate for bank loan application', 'Open', 'Normal');

-- ============================================================
-- 15. GRIEVANCES (Confidential Reports)
-- ============================================================
CREATE TABLE grievances (
    grievance_id    INT AUTO_INCREMENT PRIMARY KEY,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    title           VARCHAR(200) NOT NULL,
    description     TEXT NOT NULL,
    status          VARCHAR(50) NOT NULL DEFAULT 'Open',
    priority        VARCHAR(20) NOT NULL DEFAULT 'Confidential',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO grievances (employee_id, title, description, status, priority) VALUES
('1001', 'Confidential Inquiry', 'Query on internal harassment reporting channel', 'Open', 'Confidential');

-- ============================================================
-- 16. CALENDAR EVENTS & ABSENCE COMPATIBILITY
-- ============================================================
CREATE TABLE calendar_events (
    event_id        INT AUTO_INCREMENT PRIMARY KEY,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    title           VARCHAR(200) NOT NULL,
    event_date      DATE NOT NULL,
    event_time      VARCHAR(20) NOT NULL,
    duration        INT NOT NULL DEFAULT 30,
    location        VARCHAR(100) DEFAULT 'Meeting Room',
    attendees       TEXT,
    status          VARCHAR(50) NOT NULL DEFAULT 'Scheduled'
);

CREATE TABLE absence_requests (
    request_id      INT AUTO_INCREMENT PRIMARY KEY,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    absence_type    VARCHAR(50) NOT NULL,
    from_date       DATE NOT NULL,
    to_date         DATE NOT NULL,
    reason          VARCHAR(255),
    status          VARCHAR(50) NOT NULL DEFAULT 'Pending',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE exception_requests (
    exception_id    INT AUTO_INCREMENT PRIMARY KEY,
    request_id      INT NOT NULL,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    area_manager_id VARCHAR(10),
    exception_type  VARCHAR(50),
    status          VARCHAR(50) NOT NULL DEFAULT 'Pending',
    reason          VARCHAR(255),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 17. MANDATORY HR POLICY ACKNOWLEDGEMENTS
-- Tracks enterprise policy releases requiring employee acknowledgement
-- before a specified deadline.
-- ============================================================
CREATE TABLE policy_acknowledgements (
    ack_id              INT AUTO_INCREMENT PRIMARY KEY,
    employee_id         VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    policy_code         VARCHAR(30) NOT NULL REFERENCES hr_policies(policy_code),
    policy_name         VARCHAR(100) NOT NULL,
    announcement_text   TEXT NOT NULL,
    deadline_date       DATE NOT NULL,
    status              VARCHAR(30) NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Acknowledged', 'Overdue')),
    acknowledged_at     TIMESTAMP NULL,
    acknowledgement_note VARCHAR(255) NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY (employee_id, policy_code)
);

INSERT INTO policy_acknowledgements (employee_id, policy_code, policy_name, announcement_text, deadline_date, status) VALUES
('1014', 'WFH_HYBRID', 'Work From Home Policy', 'We have released a new Work From Home policy. All employees need to acknowledge it before Friday.', '2026-09-25', 'Pending'),
('1001', 'WFH_HYBRID', 'Work From Home Policy', 'We have released a new Work From Home policy. All employees need to acknowledge it before Friday.', '2026-09-25', 'Pending');

-- ============================================================
-- 18. SCHEDULED REMINDERS & MESSAGES
-- Tracks scheduled reminders (Form submissions, payslip downloads, etc.)
-- and records employee delivery and acknowledgements.
-- ============================================================
CREATE TABLE scheduled_reminders (
    reminder_id         INT AUTO_INCREMENT PRIMARY KEY,
    employee_id         VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    reminder_type       VARCHAR(50) NOT NULL CHECK (reminder_type IN ('FORM_SUBMISSION', 'PAYSLIP_DOWNLOAD', 'POLICY_ACKNOWLEDGEMENT', 'CUSTOM')),
    title               VARCHAR(150) NOT NULL,
    reminder_text       TEXT NOT NULL,
    scheduled_time      DATETIME NOT NULL,
    status              VARCHAR(30) NOT NULL DEFAULT 'Scheduled' CHECK (status IN ('Scheduled', 'Triggered', 'Sent', 'Acknowledged', 'Cancelled')),
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    triggered_at        TIMESTAMP NULL,
    acknowledged_at     TIMESTAMP NULL,
    employee_response   VARCHAR(255) NULL
);

INSERT INTO scheduled_reminders (employee_id, reminder_type, title, reminder_text, scheduled_time, status) VALUES
('1014', 'FORM_SUBMISSION', 'PF Nomination Form Submission', 'Good morning Mani. This is a reminder to submit your PF nomination form. Have you completed it?', '2026-09-23 10:00:00', 'Scheduled'),
('1014', 'PAYSLIP_DOWNLOAD', 'September Salary Slip Download', 'Your September salary slip is available in the employee portal. Have you downloaded it?', '2026-09-23 10:00:00', 'Scheduled'),
('1001', 'FORM_SUBMISSION', 'PF Nomination Form Submission', 'Good morning Ravikala. This is a reminder to submit your PF nomination form. Have you completed it?', '2026-09-23 10:00:00', 'Scheduled');

