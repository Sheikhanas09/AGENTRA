"use client";

import { useState } from "react";
import Layout from "../layout/Layout";

import DashboardTab from "./DashboardTab";
import EmployeeAttendance from "./EmployeeAttendance";
// import LeaveManagment from "./LeaveManagment";

import {
  FaTachometerAlt,
  FaUserAlt,
  FaFileAlt,
  FaDollarSign,
  FaSignOutAlt,
} from "react-icons/fa";

export default function EmployeeDashboard() {
  const [activeTab, setActiveTab] = useState("Dashboard");

  const tabs = [
    { name: "Dashboard", icon: <FaTachometerAlt size={20} /> },
    { name: "Attendance", icon: <FaUserAlt size={20} /> },
    { name: "Leave", icon: <FaFileAlt size={20} /> },
    { name: "Payroll", icon: <FaDollarSign size={20} /> },
  ];

  const tabComponents = {
    Dashboard: <DashboardTab />,
    Attendance: <EmployeeAttendance />,
    // Leave: <LeaveManagment />,
    Payroll: <div className="text-white">Payroll Module</div>,
  };

  return (
    <Layout
      sidebar={tabs.map((tab) => (
        <button
          key={tab.name}
          onClick={() => setActiveTab(tab.name)}
          className={`flex items-center w-full gap-3 p-3 mb-2 rounded-xl transition-all duration-300 ${
            activeTab === tab.name
              ? "text-[#05DC7F] border border-[#05DC7F]/45 shadow-[0_0_10px_rgba(5,220,127,0.4)]"
              : "text-white/65 hover:text-[#05DC7F] hover:shadow-[0_0_8px_rgba(5,220,127,0.35)]"
          }`}
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
              <p className="text-white font-medium">Employee Name</p>
              <p className="text-white/55 text-xs">Employee</p>
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
              EN
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
