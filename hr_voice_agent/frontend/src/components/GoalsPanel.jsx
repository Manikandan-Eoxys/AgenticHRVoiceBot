/**
 * hr_voice_agent/frontend/src/components/GoalsPanel.jsx
 *
 * Shows all 12 HR agent goals with live highlighting of the currently
 * active goal. Maps directly to the goals/ directory on the backend.
 */

const GOALS = [
  { id: "g01", label: "Identity Verification" },
  { id: "g02", label: "Leave Balance" },
  { id: "g03", label: "Leave Application" },
  { id: "g04", label: "Leave Status" },
  { id: "g05", label: "Attendance" },
  { id: "g06", label: "Work From Home" },
  { id: "g07", label: "Payroll" },
  { id: "g08", label: "HR Policy" },
  { id: "g09", label: "Calendar" },
  { id: "g10", label: "Reminders" },
  { id: "g11", label: "Employee Benefits" },
  { id: "g12", label: "Leave Verification (Outbound)" },
];

export default function GoalsPanel({ activeGoal }) {
  return (
    <div className="goals-panel" id="goals-panel" aria-label="HR Agent Goals">
      <div className="goals-panel__header">
        <span className="goals-panel__title">🎯 HR Agent Goals</span>
      </div>

      <div className="goals-panel__list">
        {GOALS.map(({ id, label }) => {
          const isActive =
            activeGoal &&
            (activeGoal.toLowerCase().includes(label.toLowerCase()) ||
             label.toLowerCase().includes(activeGoal.toLowerCase()));

          return (
            <div
              key={id}
              className={`goal-chip${isActive ? " goal-chip--active" : ""}`}
              aria-current={isActive ? "true" : undefined}
              title={label}
            >
              <span className="goal-chip__num">{id}</span>
              {label}
              <span className="goal-chip__dot" />
            </div>
          );
        })}
      </div>
    </div>
  );
}
