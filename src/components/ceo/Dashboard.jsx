"use client";

import { useState } from "react";
import Layout from "../layout/Layout";
import DashboardTab from "./DashboardTab";
import RecruitmentTab from "./RecruitmentTab";
import InterviewsTab from "./InterviewsTab";
import Attendance from "./Attendance";
import LeaveManagment from "./LeaveManagment";
import CreateUserTab from "./CreateUserTab";
import Hiring from "./Hiring";

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

import { HiOutlineUserGroup } from "react-icons/hi2";
import { MdOutlineTouchApp } from "react-icons/md";

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState("Dashboard");

  // ✅ Single source of truth for job posts
  const [jobPosts] = useState([
    {
      id: 1,
      title: "Frontend Developer",
      postedAt: "2026-02-01 10:30 AM",
      description: `Frontend description`,
    },
    {
      id: 2,
      title: "Backend Developer",
      postedAt: "2026-01-29 02:15 PM",
      description: `Backend description`,
    },
    {
      id: 3,
      title: "UI/UX Designer",
      postedAt: "2026-01-25 11:00 AM",
      description: `UI/UX description`,
    },
    {
      id: 4,
      title: "AI Engineer",
      postedAt: "2026-01-25 11:00 AM",
      description: `AI description`,
    },
  ]);

  const tabs = [
    { name: "Dashboard", icon: <FaTachometerAlt size={20} /> },
    { name: "Recruitment", icon: <FaBriefcase size={20} /> },
    { name: "Interviews", icon: <FaCalendarCheck size={20} /> },
    { name: "Hiring", icon: <MdOutlineTouchApp size={20} /> },
    { name: "Create Employee", icon: <HiOutlineUserGroup size={20} /> },
    { name: "Attendance", icon: <FaUserAlt size={20} /> },
    { name: "Leave Managment", icon: <FaFileAlt size={20} /> },
    { name: "Payroll", icon: <FaDollarSign size={20} /> },
    { name: "Analytics", icon: <FaChartBar size={20} /> },
  ];

  const tabComponents = {
    Dashboard: <DashboardTab />,
    Recruitment: <RecruitmentTab jobPosts={jobPosts} />, // ✅ pass jobPosts here
    Interviews: <InterviewsTab />,
    Hiring: <Hiring jobPosts={jobPosts} />, // ✅ pass jobPosts here
    "Create Employee": <CreateUserTab />,
    Attendance: <Attendance />,
    "Leave Managment": <LeaveManagment />,
    Payroll: <div>Payroll Module</div>,
    Analytics: <div>Analytics Module</div>,
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
