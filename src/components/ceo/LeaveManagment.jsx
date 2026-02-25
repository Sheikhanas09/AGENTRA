"use client";

import { useState } from "react";
import {
  FaHourglassHalf,
  FaCheckCircle,
  FaCalendarCheck,
  FaTimesCircle,
  FaEye,
  FaTimes,
  FaThumbsUp,
  FaThumbsDown,
} from "react-icons/fa";

/* ================= LEAVE STAT CARD ================= */
function LeaveStatCard({ title, value, valueColor, icon }) {
  return (
    <div
      className="flex justify-between items-center p-5 rounded-xl
      backdrop-blur-sm border border-[#05DC7F]/25
      shadow-[0_0_8px_rgba(5,220,127,0.25)]
      hover:border-[#05DC7F]/45
      hover:shadow-[0_0_18px_rgba(5,220,127,0.45)]
      transition-all duration-300 min-w-0"
    >
      <div className="min-w-0">
        <p className="text-gray-400 text-sm truncate">{title}</p>
        <h3 className={`text-3xl font-bold ${valueColor} truncate`}>{value}</h3>
      </div>
      <div
        className="w-12 h-12 rounded-full flex items-center justify-center
        bg-[#05DC7F]/15 border border-[#05DC7F]/40 flex-shrink-0"
      >
        {icon}
      </div>
    </div>
  );
}

/* ================= STATUS COLOR ================= */
const statusStyles = {
  Pending: "bg-yellow-500/15 text-yellow-400 border border-yellow-500/40",
  Approved: "bg-[#05DC7F]/15 text-[#05DC7F] border border-[#05DC7F]/40",
  Rejected: "bg-red-500/15 text-red-400 border border-red-500/40",
};

/* ================= LEAVE REQUEST CARD ================= */
function LeaveRequestCard({ data, onView }) {
  return (
    <div
      className="flex flex-col lg:flex-row justify-between gap-6
      p-6 rounded-2xl bg-black/40 backdrop-blur-sm
      border border-[#05DC7F]/25
      shadow-[0_0_10px_rgba(5,220,127,0.15)]
      hover:border-[#05DC7F]/45 transition-all min-w-0"
    >
      <div className="flex flex-col gap-3 min-w-0">
        <h3 className="text-white text-lg font-semibold truncate">
          {data.name}
        </h3>

        <div className="flex flex-wrap gap-3">
          <span
            className="px-3 py-1 text-xs rounded-full
            bg-[#05DC7F]/15 text-[#05DC7F]
            border border-[#05DC7F]/40 truncate"
          >
            {data.leaveType}
          </span>

          <span
            className={`px-3 py-1 text-xs rounded-full ${statusStyles[data.status]} truncate`}
          >
            {data.status}
          </span>
        </div>

        <p className="text-gray-400 text-sm truncate">
          Department: {data.department}
        </p>
      </div>

      <div className="flex items-center flex-shrink-0">
        <button
          onClick={() => onView(data)}
          className="flex items-center gap-2 px-5 py-2.5
          rounded-xl bg-[#05DC7F]
          hover:bg-[#04c56f]
          text-black font-semibold transition whitespace-nowrap"
        >
          <FaEye />
          Details
        </button>
      </div>
    </div>
  );
}

/* ================= MAIN COMPONENT ================= */
export default function LeaveManagment() {
  const [leaveRequests, setLeaveRequests] = useState([
    {
      id: 1,
      name: "Ali Khan",
      leaveType: "Sick Leave",
      status: "Pending",
      department: "Development",
      from: "10 Feb 2026",
      to: "12 Feb 2026",
      duration: "3 Days",
      reason:
        "I am suffering from high fever and doctor has advised complete rest for 3 days. Medical certificate attached.",
    },
    {
      id: 2,
      name: "Sara Ahmed",
      leaveType: "Annual Leave",
      status: "Pending",
      department: "HR",
      from: "15 Feb 2026",
      to: "20 Feb 2026",
      duration: "6 Days",
      reason:
        "Family trip planned earlier this year. I will ensure all my responsibilities are handed over properly before leave.",
    },
    {
      id: 3,
      name: "Sara Ahmed",
      leaveType: "Annual Leave",
      status: "Pending",
      department: "HR",
      from: "15 Feb 2026",
      to: "20 Feb 2026",
      duration: "6 Days",
      reason:
        "Family trip planned earlier this year. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my respear. I will ensure all my responsibilities are handed over properly before leave.",
    },
  ]);

  const [selected, setSelected] = useState(null);

  /* ===== PAGINATION ===== */
  const itemsPerPage = 2;
  const [currentPage, setCurrentPage] = useState(1);

  const totalPages = Math.ceil(leaveRequests.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentItems = leaveRequests.slice(
    startIndex,
    startIndex + itemsPerPage,
  );

  const goToPage = (page) => {
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    setCurrentPage(page);
  };

  const updateStatus = (id, newStatus) => {
    setLeaveRequests((prev) =>
      prev.map((item) =>
        item.id === id ? { ...item, status: newStatus } : item,
      ),
    );
    setSelected(null);
  };

  return (
    <div className="flex flex-col gap-10 px-4 sm:px-6 lg:px-0">
      {/* ===== TOP CARDS ===== */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        <LeaveStatCard
          title="Pending Requests"
          value="12"
          valueColor="text-yellow-400"
          icon={<FaHourglassHalf className="text-[#05DC7F] text-xl" />}
        />
        <LeaveStatCard
          title="Auto-Approved"
          value="8"
          valueColor="text-[#05DC7F]"
          icon={<FaCheckCircle className="text-[#05DC7F] text-xl" />}
        />
        <LeaveStatCard
          title="Approved This Month"
          value="34"
          valueColor="text-white"
          icon={<FaCalendarCheck className="text-[#05DC7F] text-xl" />}
        />
        <LeaveStatCard
          title="Rejected This Month"
          value="5"
          valueColor="text-red-500"
          icon={<FaTimesCircle className="text-[#05DC7F] text-xl" />}
        />
      </div>

      {/* ===== REQUEST CARDS ===== */}
      <div className="flex flex-col gap-6">
        {currentItems.map((item) => (
          <LeaveRequestCard key={item.id} data={item} onView={setSelected} />
        ))}
      </div>

      {/* ===== PAGINATION ===== */}
      <div className="flex flex-wrap justify-end items-center gap-2 text-gray-300 text-sm mt-4">
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
        <span>
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

      {/* ===== FULL SCREEN MODAL ===== */}
      {selected && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-md flex items-center justify-center p-4 sm:p-6">
          <div className="w-full max-w-full sm:max-w-3xl bg-black border border-[#05DC7F]/40 rounded-2xl p-6 sm:p-8 max-h-[90vh] overflow-y-auto relative">
            <button
              onClick={() => setSelected(null)}
              className="absolute top-4 right-4 text-gray-400 hover:text-white"
            >
              <FaTimes size={20} />
            </button>

            <h2 className="text-white text-2xl font-bold mb-6 truncate">
              {selected.name}
            </h2>

            <div className="space-y-4 text-gray-300">
              <p>
                <span className="text-white">Duration:</span>{" "}
                {selected.duration}
              </p>
              <p>
                <span className="text-white">From:</span> {selected.from}
              </p>
              <p>
                <span className="text-white">To:</span> {selected.to}
              </p>

              <div>
                <p className="text-white mb-2">Reason:</p>
                <div className="bg-black/40 p-4 rounded-lg border border-gray-700 max-h-40 overflow-y-auto overflow-x-hidden break-words">
                  {selected.reason}
                </div>
              </div>
            </div>

            <div className="flex flex-wrap gap-4 mt-8 justify-end">
              <button
                onClick={() => updateStatus(selected.id, "Approved")}
                className="flex items-center gap-2 px-5 py-2 rounded-xl bg-[#05DC7F] hover:bg-[#04c56f] text-black font-semibold transition flex-shrink-0"
              >
                <FaThumbsUp />
                Approve
              </button>

              <button
                onClick={() => updateStatus(selected.id, "Rejected")}
                className="flex items-center gap-2 px-5 py-2 rounded-xl bg-red-600 hover:bg-red-500 text-white font-semibold transition flex-shrink-0"
              >
                <FaThumbsDown />
                Reject
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
