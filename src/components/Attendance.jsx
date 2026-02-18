"use client";

import { useState, useRef } from "react";
import {
  FaUsers,
  FaUserCheck,
  FaUserTimes,
  FaClock,
  FaMapMarkerAlt,
  FaCalendarAlt,
} from "react-icons/fa";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

/* ================= CUSTOM TOOLTIP ================= */
function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;

  return (
    <div className="bg-black/90 backdrop-blur-md px-4 py-2 rounded-lg border border-[#05DC7F]/30 shadow-lg">
      <p className="text-gray-400 text-xs">Present Employees</p>
      <p className="text-white font-semibold">{payload[0].value}</p>
    </div>
  );
}

/* ================= ATTENDANCE STAT CARD ================= */
function AttendanceStatCard({ title, value, valueColor, icon }) {
  return (
    <div
      className="flex justify-between items-center p-5 rounded-xl
      backdrop-blur-sm border border-[#05DC7F]/25
      shadow-[0_0_8px_rgba(5,220,127,0.25)]
      hover:border-[#05DC7F]/45
      hover:shadow-[0_0_18px_rgba(5,220,127,0.45)]
      transition-all duration-300"
    >
      <div>
        <p className="text-gray-400 text-sm">{title}</p>
        <h3 className={`text-3xl font-bold ${valueColor}`}>{value}</h3>
      </div>

      <div
        className="w-12 h-12 rounded-full flex items-center justify-center
        bg-[#05DC7F]/15 border border-[#05DC7F]/40"
      >
        {icon}
      </div>
    </div>
  );
}

/* ================= EMPLOYEE DATA ================= */
const employeesData = [
  {
    name: "Sarah Chen",
    department: "Engineering",
    checkIn: "09:02 AM",
    checkOut: "06:15 PM",
    location: "Verified",
    face: "Verified",
    status: "Present",
  },
  {
    name: "Michael Rodriguez",
    department: "Engineering",
    checkIn: "08:55 AM",
    checkOut: "06:00 PM",
    location: "Verified",
    face: "Verified",
    status: "Present",
  },
  {
    name: "Emily Johnson",
    department: "Product",
    checkIn: "09:10 AM",
    checkOut: "-",
    location: "Verified",
    face: "Verified",
    status: "Present",
  },
  {
    name: "James Wilson",
    department: "Design",
    checkIn: "-",
    checkOut: "-",
    location: "-",
    face: "N/A",
    status: "Absent",
  },
  {
    name: "Lisa Anderson",
    department: "Marketing",
    checkIn: "09:30 AM",
    checkOut: "-",
    location: "Verified",
    face: "Verified",
    status: "Late",
  },
];

/* ================= FINAL COMPONENT ================= */
export default function Attendance() {
  const [activeView, setActiveView] = useState("weekly");
  const [filter, setFilter] = useState("All");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const dateRef = useRef(null);
  const itemsPerPage = 3;
  const [currentPage, setCurrentPage] = useState(1);

  const weeklyData = [
    { name: "Mon", present: 90 },
    { name: "Tue", present: 95 },
    { name: "Wed", present: 92 },
    { name: "Thu", present: 97 },
    { name: "Fri", present: 94 },
  ];

  const monthlyData = [
    { name: "Week 1", present: 420 },
    { name: "Week 2", present: 445 },
    { name: "Week 3", present: 430 },
    { name: "Week 4", present: 460 },
  ];

  const chartData = activeView === "weekly" ? weeklyData : monthlyData;

  const filteredEmployees =
    filter === "All"
      ? employeesData
      : employeesData.filter((e) => e.status === filter);

  const totalPages = Math.ceil(filteredEmployees.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentEmployees = filteredEmployees.slice(
    startIndex,
    startIndex + itemsPerPage,
  );

  const goToPage = (page) => {
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    setCurrentPage(page);
  };

  const statusBadge = {
    Present: "bg-[#05DC7F]/20 text-[#05DC7F] border border-[#05DC7F]/40",
    Absent: "bg-red-500/20 text-red-400 border border-red-500/40",
    Late: "bg-yellow-500/20 text-yellow-400 border border-yellow-500/40",
  };

  return (
    <div className="flex flex-col gap-10">
      {/* ===== TOP ATTENDANCE STATS ===== */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        <AttendanceStatCard
          title="Total Employees"
          value="120"
          valueColor="text-white"
          icon={<FaUsers className="text-[#05DC7F] text-xl" />}
        />
        <AttendanceStatCard
          title="Present Today"
          value="96"
          valueColor="text-[#05DC7F]"
          icon={<FaUserCheck className="text-[#05DC7F] text-xl" />}
        />
        <AttendanceStatCard
          title="Absentees"
          value="18"
          valueColor="text-red-500"
          icon={<FaUserTimes className="text-[#05DC7F] text-xl" />}
        />
        <AttendanceStatCard
          title="Late Arrivals"
          value="6"
          valueColor="text-yellow-400"
          icon={<FaClock className="text-[#05DC7F] text-xl" />}
        />
      </div>

      {/* ===== ATTENDANCE CHART ===== */}
      <div
        className="bg-black/30 rounded-2xl p-5 sm:p-6
        backdrop-blur-sm border border-[#05DC7F]/20
        shadow-[0_0_10px_rgba(5,220,127,0.15)]
        flex flex-col gap-6"
      >
        <div className="flex flex-wrap justify-between items-center gap-4">
          <h2 className="text-white text-xl font-semibold">
            Attendance Overview
          </h2>

          <div className="flex gap-2">
            <button
              onClick={() => setActiveView("weekly")}
              className={`px-4 py-2 rounded-full text-sm font-semibold transition
                ${
                  activeView === "weekly"
                    ? "bg-[#05DC7F]/20 text-white border border-[#05DC7F]"
                    : "text-gray-400 hover:bg-[#05DC7F]/10"
                }`}
            >
              Weekly
            </button>

            <button
              onClick={() => setActiveView("monthly")}
              className={`px-4 py-2 rounded-full text-sm font-semibold transition
                ${
                  activeView === "monthly"
                    ? "bg-[#05DC7F]/20 text-white border border-[#05DC7F]"
                    : "text-gray-400 hover:bg-[#05DC7F]/10"
                }`}
            >
              Monthly
            </button>
          </div>
        </div>

        <div className="w-full h-[280px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <XAxis dataKey="name" stroke="#9CA3AF" />
              <YAxis stroke="#9CA3AF" />
              <Tooltip content={<CustomTooltip />} />
              <Line
                type="monotone"
                dataKey="present"
                stroke="#05DC7F"
                strokeWidth={3}
                dot={{ r: 4 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* ===== EMPLOYEE TABLE ===== */}
      <div className="rounded-2xl bg-black/40 backdrop-blur-sm border border-[#05DC7F]/25 p-4 md:p-6">
        {/* Header */}
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center mb-4 md:mb-6 gap-4 md:gap-0">
          <div className="flex items-center gap-3">
            <h2 className="text-white text-base md:text-lg font-semibold">
              Today’s Attendance –{" "}
              <span className="text-gray-400 font-normal ml-2 text-sm md:text-base">
                {new Date(date).toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                  year: "numeric",
                })}
              </span>
            </h2>

            <input
              ref={dateRef}
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="hidden"
            />

            <button
              onClick={() => dateRef.current?.showPicker()}
              className="p-2 rounded-lg bg-[#05DC7F]/15 border border-[#05DC7F]/40 text-[#05DC7F]"
            >
              <FaCalendarAlt />
            </button>
          </div>

          <div className="flex flex-wrap gap-2">
            {["Present", "Absent", "Late"].map((s) => (
              <button
                key={s}
                onClick={() => {
                  setFilter(filter === s ? "All" : s);
                  setCurrentPage(1);
                }}
                className={`px-3 py-1 md:px-4 md:py-1.5 rounded-full text-xs md:text-sm font-semibold transition
            ${
              filter === s
                ? statusBadge[s]
                : "text-gray-400 border border-gray-700 hover:bg-gray-800"
            }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {/* Table */}
        <div className="overflow-x-auto w-full">
          <table className="min-w-[700px] md:min-w-full w-full border-collapse table-fixed">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Employee
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Department
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Check-In
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Check-Out
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Location
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Face Verification
                </th>
                <th className="py-2 md:py-3 px-3 md:px-4 text-left text-xs md:text-sm lg:text-base">
                  Status
                </th>
              </tr>
            </thead>

            <tbody>
              {currentEmployees.map((emp, i) => (
                <tr
                  key={i}
                  className="border-b border-gray-700 hover:bg-[#05DC7F]/10 transition"
                >
                  <td className="py-2 md:py-4 px-3 md:px-4">
                    <div className="flex items-center gap-2 md:gap-3">
                      <div className="w-9 h-9 md:w-11 md:h-11 -ml-2 rounded-full bg-[#05DC7F]/20 flex items-center justify-center text-[#05DC7F] font-semibold text-sm md:text-base shrink-0">
                        {emp.name
                          .split(" ")
                          .map((n) => n[0])
                          .join("")}
                      </div>
                      <span className="text-white text-sm md:text-base">
                        {emp.name}
                      </span>
                    </div>
                  </td>

                  <td className="py-2 md:py-4 px-3 md:px-4 text-gray-300 text-xs md:text-sm lg:text-base">
                    {emp.department}
                  </td>

                  <td className="py-2 md:py-4 px-3 md:px-4 text-gray-300 text-xs md:text-sm lg:text-base">
                    <div className="flex items-center gap-1 md:gap-2">
                      <FaClock className="text-[#05DC7F]" />
                      {emp.checkIn}
                    </div>
                  </td>

                  <td className="py-2 md:py-4 px-3 md:px-4 text-gray-300 text-xs md:text-sm lg:text-base">
                    <div className="flex items-center gap-1 md:gap-2">
                      <FaClock className="text-[#05DC7F]" />
                      {emp.checkOut}
                    </div>
                  </td>

                  <td className="py-2 md:py-4 px-3 md:px-4 text-gray-300 text-xs md:text-sm lg:text-base">
                    <div className="flex items-center gap-1 md:gap-2">
                      <FaMapMarkerAlt className="text-[#05DC7F]" />
                      {emp.location}
                    </div>
                  </td>

                  <td className="py-2 md:py-4 px-3 md:px-4 text-gray-300 text-xs md:text-sm lg:text-base">
                    {emp.face}
                  </td>

                  <td className="py-2 md:py-4 px-3 md:px-4">
                    <span
                      className={`px-2 md:px-3 py-1 rounded-full text-xs md:text-sm font-semibold ${statusBadge[emp.status]}`}
                    >
                      {emp.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex flex-wrap gap-1 justify-end items-center mt-3 md:mt-4 text-gray-300 text-xs md:text-sm">
          <button
            onClick={() => goToPage(1)}
            className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
          >
            «
          </button>
          <button
            onClick={() => goToPage(currentPage - 1)}
            className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
          >
            ‹
          </button>

          <span className="px-2 py-1 md:px-3">
            Page {currentPage} of {totalPages}
          </span>

          <button
            onClick={() => goToPage(currentPage + 1)}
            className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
          >
            ›
          </button>
          <button
            onClick={() => goToPage(totalPages)}
            className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
          >
            »
          </button>
        </div>
      </div>
    </div>
  );
}
