// Dashboard.jsx
"use client";

import { useState } from "react";
import Layout from "./Layout";
import DashboardTab from "./DashboardTab";
import RecruitmentTab from "./RecruitmentTab";
import {
  FaTachometerAlt,
  FaBriefcase,
  FaCalendarCheck,
  FaUserAlt,
  FaFileAlt,
  FaDollarSign,
  FaChartBar,
  FaSignOutAlt,
} from "react-icons/fa";

/* ================= PLACEHOLDER TAB COMPONENTS ================= */
function DashboardHome() {
  return (
    <div className="text-white/70">
      <DashboardTab />
    </div>
  );
}

function Recruitment() {
  return (
    <div className="text-white/70">
      <RecruitmentTab />
    </div>
  );
}

function Interviews() {
  return <div className="text-white/70">Interviews Module</div>;
}

function Attendance() {
  return <div className="text-white/70">Attendance Module</div>;
}

function LeaveManagement() {
  return <div className="text-white/70">Leave Management Module</div>;
}

function Payroll() {
  return <div className="text-white/70">Payroll Module</div>;
}

function Analytics() {
  return <div className="text-white/70">Analytics Module</div>;
}

/* ================= MAIN DASHBOARD ================= */
export default function Dashboard() {
  const [activeTab, setActiveTab] = useState("Dashboard");

  const tabs = [
    { name: "Dashboard", icon: <FaTachometerAlt size={20} /> },
    { name: "Recruitment", icon: <FaBriefcase size={20} /> },
    { name: "Interviews", icon: <FaCalendarCheck size={20} /> },
    { name: "Attendance", icon: <FaUserAlt size={20} /> },
    { name: "Leave Management", icon: <FaFileAlt size={20} /> },
    { name: "Payroll", icon: <FaDollarSign size={20} /> },
    { name: "Analytics", icon: <FaChartBar size={20} /> },
  ];

  const tabComponents = {
    Dashboard: <DashboardHome />,
    Recruitment: <Recruitment />,
    Interviews: <Interviews />,
    Attendance: <Attendance />,
    "Leave Management": <LeaveManagement />,
    Payroll: <Payroll />,
    Analytics: <Analytics />,
  };

  return (
    <Layout
      sidebar={tabs.map((tab) => (
        <button
          key={tab.name}
          onClick={() => setActiveTab(tab.name)}
          className={`
            flex items-center w-full gap-3 p-3 mb-2 rounded-xl
            transition-all duration-300
            ${
              activeTab === tab.name
                ? "text-[#05DC7F] border border-[#05DC7F]/45 shadow-[0_0_10px_rgba(5,220,127,0.4)]"
                : "text-white/65 hover:text-[#05DC7F] hover:shadow-[0_0_8px_rgba(5,220,127,0.35)]"
            }
          `}
        >
          {tab.icon}
          <span className="tracking-wide whitespace-nowrap text-sm">
            {tab.name}
          </span>
        </button>
      ))}
      navbar={
        <div
          className="
            flex justify-between items-center
            mb-6 p-4 rounded-xl
            border border-[#05DC7F]/35
            shadow-[0_0_10px_rgba(5,220,127,0.35)]
            backdrop-blur-sm
            flex-wrap md:flex-nowrap
          "
        >
          <h2 className="text-white text-xl font-semibold tracking-wider whitespace-nowrap">
            {activeTab}
          </h2>

          <div className="flex items-center gap-4 mt-2 md:mt-0">
            <div className="text-right">
              <p className="text-white font-medium">Sheikh Anas</p>
              <p className="text-white/55 text-xs">CEO / Administrator</p>
            </div>

            <div
              className="
                w-10 h-10 rounded-full
                bg-[#05DC7F]
                text-black font-bold
                flex items-center justify-center
                shadow-[0_0_10px_rgba(5,220,127,0.4)]
              "
            >
              SA
            </div>

            <button className="text-[#05DC7F]/65 hover:text-white transition">
              <FaSignOutAlt size={22} />
            </button>
          </div>
        </div>
      }
      content={tabComponents[activeTab]}
    />
  );
}
