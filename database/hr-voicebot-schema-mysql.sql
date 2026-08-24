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
    phone_number   VARCHAR(20)
);

INSERT INTO employees (employee_id, full_name, gender, email, department_id, designation_id, join_date, status, phone_number) VALUES
('1001', 'Ravikala',    'F', 'ravikala@eoxys.com',    1, 1, '2023-03-31', 'active',   '9500000001'),
('1002', 'Deepika',     'F', 'deepika@efabric.com',     1, 2, '2022-12-11', 'on_leave', '9500000002'),      
('1003', 'Narmatha',    'F', 'narmatha@efabric.com',    1, 1, '2023-09-01', 'active',   '9500000003'),
('1004', 'Hashir',      'M', 'hashir@efabric.com',      1, 1, '2024-02-21', 'active',   '9500000004'),
('1005', 'Saikumar',    'M', 'saikumar@efabric.com',    1, 1, '2024-02-21', 'active',   '9500000005'),
('1006', 'Bharath',     'M', 'bharath@efabric.com',     1, 1, '2024-02-21', 'active',   '9500000006'),
('1007', 'Guna',        'M', 'guna@efabric.com',        1, 2, '2023-12-21', 'active',   '9500000007'),
('1008', 'Shivaraj',    'M', 'shivaraj@efabric.com',    1, 1, '2025-02-01', 'active',   '9500000008'),
('1009', 'Govindaraj',  'M', 'govindaraj@efabric.com',  1, 1, '2025-02-01', 'active',   '9500000009'),
('1010', 'Charan',      'M', 'charan@efabric.com',      2, 3, '2024-07-01', 'active',   '9500000010'),
('1011', 'Momin',       'M', 'momin@efabric.com',       2, 3, '2023-09-06', 'active',   '9500000011'),
('1012', 'Satheesh',    'M', 'satheesh@efabric.com',    3, 4, '2023-04-07', 'active',   '9500000012'),
('1013', 'Kamal',       'M', 'kamal@efabric.com',       1, 1, '2025-09-05', 'active',   '9786586806'),
('1014', 'Manikandan',  'M', 'manikandan@efabric.com',  1, 1, '2025-09-05', 'active',   '8925355704'),
('1015', 'Sujitsaju',   'M', 'sujitsaju@efabric.com',   3, 4, '2023-04-07', 'active',   '9500000015');

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
-- 7. LEAVE REQUESTS — ledger for requests submitted through the
-- voice agent's check-availability -> confirm -> email flow.
-- Confirming a request increments leave_balances.used_days for the
-- matching employee/leave_code.
-- ============================================================
CREATE TABLE leave_requests (
    request_id      INT AUTO_INCREMENT PRIMARY KEY,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    leave_code      VARCHAR(20) NOT NULL REFERENCES leave_policy(leave_code),
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    days_requested  INT NOT NULL,
    reason          VARCHAR(255),
    status          VARCHAR(20) NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','approved','rejected')),
    notified_email  VARCHAR(150),
    requested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 8. CALENDAR EVENTS
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

-- ============================================================
-- 9. GRIEVANCES
-- ============================================================
CREATE TABLE grievances (
    grievance_id    INT AUTO_INCREMENT PRIMARY KEY,
    employee_id     VARCHAR(10) NOT NULL REFERENCES employees(employee_id),
    title           VARCHAR(200) NOT NULL,
    description     TEXT NOT NULL,
    status          VARCHAR(50) NOT NULL DEFAULT 'Open',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 10. ABSENCE & EXCEPTION REQUESTS
-- ============================================================
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

