import { useState } from "react";
import { CheckCircle2, LogOut, CalendarDays, DollarSign } from "lucide-react";

// Reusable small card component
const StatCard = ({ title, value, subtitle, icon }) => (
  <div
    className="p-5 rounded-xl backdrop-blur-sm border border-[#05DC7F]/25 
    shadow-[0_0_8px_rgba(5,220,127,0.25)] 
    hover:border-[#05DC7F]/45 
    hover:shadow-[0_0_18px_rgba(5,220,127,0.45)] 
    transition-all duration-300"
  >
    <div className="flex justify-between items-center">
      <div>
        <p className="text-gray-400 text-sm">{title}</p>
        <h3 className="text-white text-2xl font-bold mt-1">{value}</h3>
        {subtitle && <p className="text-gray-500 text-xs">{subtitle}</p>}
      </div>
      <div
        className="w-12 h-12 rounded-full flex items-center justify-center 
        bg-[#05DC7F]/15 border border-[#05DC7F]/40"
      >
        {icon}
      </div>
    </div>
  </div>
);

export default function DashboardTab() {
  const [checkedIn, setCheckedIn] = useState(true);

  // Later you can make these dynamic
  const attendanceStats = {
    totalDays: 22,
    present: 20,
    absent: 1,
    late: 1,
    rate: 91,
  };

  return (
    <div className="w-full flex flex-col gap-8">
      {/* ==== TOP GRID ==== */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Today's Attendance Card */}
        <div
          className="lg:col-span-2 p-6 rounded-xl backdrop-blur-sm border border-[#05DC7F]/25 
          shadow-[0_0_8px_rgba(5,220,127,0.25)] 
          hover:border-[#05DC7F]/45 
          hover:shadow-[0_0_18px_rgba(5,220,127,0.45)] 
          transition-all duration-300"
        >
          <h2 className="text-white text-lg font-semibold mb-6">
            Today's Attendance
          </h2>

          <div className="flex items-center gap-4 mb-6">
            <div
              className="w-14 h-14 rounded-full flex items-center justify-center 
              bg-[#05DC7F]/15 border border-[#05DC7F]/40"
            >
              <CheckCircle2 size={26} className="text-[#05DC7F]" />
            </div>

            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="h-2 w-2 bg-[#05DC7F] rounded-full animate-pulse"></span>
                <span className="text-[#05DC7F] font-medium text-sm">
                  {checkedIn ? "Checked In" : "Checked Out"}
                </span>
              </div>
              <p className="text-gray-400 text-xs">
                Check-in time: <span className="text-white">09:02 AM</span>
              </p>
            </div>
          </div>

          <button
            onClick={() => setCheckedIn(!checkedIn)}
            className="flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg 
              border border-[#05DC7F]/40 
              text-[#05DC7F] text-sm font-medium
              hover:bg-[#05DC7F] hover:text-black
              transition-all duration-300"
          >
            <LogOut size={14} />
            {checkedIn ? "Check Out" : "Check In"}
          </button>
        </div>

        {/* Right Stack */}
        <div className="flex flex-col gap-6">
          <StatCard
            title="Leave Balance"
            value="12"
            subtitle="days remaining"
            icon={<CalendarDays size={20} className="text-[#05DC7F]" />}
          />
          <StatCard
            title="This Month"
            value="$8.5K"
            subtitle="salary"
            icon={<DollarSign size={20} className="text-[#05DC7F]" />}
          />
        </div>
      </div>

      {/* ==== Monthly Attendance Card ==== */}
      <div
        className="p-6 rounded-xl backdrop-blur-sm border border-[#05DC7F]/25 
  shadow-[0_0_8px_rgba(5,220,127,0.25)] 
  hover:border-[#05DC7F]/45 
  hover:shadow-[0_0_18px_rgba(5,220,127,0.45)] 
  transition-all duration-300 w-full text-lg"
      >
        <h2 className="text-white text-xl font-semibold mb-6">
          This Month Attendance
        </h2>

        {/* Stats Row */}
        <div className="grid grid-cols-4 gap-6 mb-2 text-left">
          <div>
            <p className="text-gray-400 text-base">Total Days</p>
            <h3 className="text-white text-2xl font-bold">
              {attendanceStats.totalDays}
            </h3>
          </div>
          <div>
            <p className="text-gray-400 text-base">Present</p>
            <h3 className="text-[#05DC7F] text-2xl font-bold">
              {attendanceStats.present}
            </h3>
          </div>
          <div>
            <p className="text-gray-400 text-base">Absent</p>
            <h3 className="text-red-500 text-2xl font-bold">
              {attendanceStats.absent}
            </h3>
          </div>
          <div>
            <p className="text-gray-400 text-base">Late</p>
            <h3 className="text-yellow-400 text-2xl font-bold">
              {attendanceStats.late}
            </h3>
          </div>
        </div>

        {/* Theme Line */}
        <div className="h-[2px] w-full bg-[#05DC7F]/40 mb-6"></div>

        {/* Attendance Rate + Progress Bar */}
        <div className="flex items-center justify-between mt-2">
          <div>
            <p className="text-gray-400 text-base">Attendance Rate</p>
            <h3 className="text-white text-3xl font-bold">
              {attendanceStats.rate}%
            </h3>
          </div>
          <div className="w-48 h-3 bg-[#05DC7F]/10 rounded-full overflow-hidden">
            <div
              className="h-full bg-[#05DC7F] transition-all duration-700"
              style={{ width: `${attendanceStats.rate}%` }}
            ></div>
          </div>
        </div>
      </div>

      {/* ==== Recent Activity Card ==== */}
      <div
        className="p-6 rounded-xl backdrop-blur-sm border border-[#05DC7F]/25
  shadow-[0_0_8px_rgba(5,220,127,0.25)]
  hover:border-[#05DC7F]/45
  hover:shadow-[0_0_18px_rgba(5,220,127,0.45)]
  transition-all duration-300 w-full"
      >
        <h2 className="text-white text-xl font-semibold mb-6">
          Recent Activity
        </h2>

        <div className="flex flex-col gap-4">
          {[
            {
              title: "Checked In",
              subtitle: "Today at 09:02 AM",
              icon: <CheckCircle2 size={20} className="text-[#05DC7F]" />,
              bg: "bg-[#05DC7F]/15",
              border: "border-[#05DC7F]/40",
            },
            {
              title: "Leave Request Approved",
              subtitle: "Yesterday",
              icon: <CalendarDays size={20} className="text-[#05DC7F]" />,
              bg: "bg-[#05DC7F]/15",
              border: "border-[#05DC7F]/40",
            },
            {
              title: "Payslip Generated",
              subtitle: "2 days ago",
              icon: <DollarSign size={20} className="text-[#05DC7F]" />,
              bg: "bg-[#05DC7F]/15",
              border: "border-[#05DC7F]/40",
            },
          ].map((item, index) => (
            <div
              key={index}
              className={`flex items-center gap-4 p-4 rounded-xl backdrop-blur-sm border ${item.border} 
        shadow-[0_0_8px_rgba(5,220,127,0.25)]
        hover:border-[#05DC7F]/45 hover:shadow-[0_0_18px_rgba(5,220,127,0.45)]
        transition-all duration-300`}
            >
              <div
                className={`w-10 h-10 flex items-center justify-center rounded-full ${item.bg} border ${item.border}`}
              >
                {item.icon}
              </div>
              <div className="flex flex-col">
                <span className="text-white font-medium">{item.title}</span>
                <span className="text-gray-400 text-xs">{item.subtitle}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
