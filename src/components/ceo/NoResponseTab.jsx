"use client";

import { useState } from "react";
import {
  FaCalendarAlt,
  FaClock,
  FaVideo,
  FaMapMarkerAlt,
  FaInfoCircle,
  FaTimes,
  FaUserTie,
  FaExclamationTriangle,
} from "react-icons/fa";

/* ================= INTERVIEW CARD ================= */
function InterviewCard({ item, onView }) {
  const { name, position, date, day, time, type } = item;

  return (
    <div
      className="flex flex-col lg:flex-row justify-between gap-6
        p-6 rounded-2xl
        bg-black/40 backdrop-blur-sm
        border border-[#05DC7F]/25
        shadow-[0_0_10px_rgba(5,220,127,0.15)]
        hover:border-[#05DC7F]/45
        transition-all"
    >
      <div className="flex flex-col gap-3">
        <h3 className="text-white text-lg font-semibold">{name}</h3>

        <p className="text-gray-400">{position}</p>

        <div className="flex flex-wrap gap-5 text-gray-300 text-sm">
          <div className="flex items-center gap-2">
            <FaCalendarAlt className="text-[#05DC7F]" />
            {date} ({day})
          </div>

          <div className="flex items-center gap-2">
            <FaClock className="text-[#05DC7F]" />
            {time}
          </div>

          <div className="flex items-center gap-2">
            {type === "Video Call" ? (
              <FaVideo className="text-[#05DC7F]" />
            ) : (
              <FaMapMarkerAlt className="text-[#05DC7F]" />
            )}
            {type}
          </div>
        </div>
      </div>

      <div className="flex items-center">
        <button
          onClick={() => onView(item)}
          className="flex items-center gap-2 px-5 py-2.5
            rounded-xl bg-[#05DC7F]
            hover:bg-[#04c56f]
            text-black font-semibold transition"
        >
          <FaInfoCircle />
          Details
        </button>
      </div>
    </div>
  );
}

/* ================= DETAILS MODAL ================= */
function DetailsModal({ data, onClose }) {
  if (!data) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-2xl bg-black/90 border border-[#05DC7F]/40 p-6 relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-gray-400 hover:text-white"
        >
          <FaTimes />
        </button>

        <h2 className="text-white text-xl font-bold mb-5 text-center">
          Interview Details
        </h2>

        <div className="flex flex-col gap-4 text-sm text-gray-300">
          <p className="flex items-center gap-2">
            <FaUserTie className="text-[#05DC7F]" />
            <span className="text-white font-semibold">{data.name}</span>
          </p>

          <p>
            Position:{" "}
            <span className="text-white font-medium">{data.position}</span>
          </p>

          <p>
            Schedule:{" "}
            <span className="text-white font-medium">
              {data.date} ({data.day}) | {data.time}
            </span>
          </p>

          <p className="flex items-center gap-2">
            <FaExclamationTriangle className="text-yellow-400" />
            <span className="text-white font-medium">{data.noShowReason}</span>
          </p>
        </div>
      </div>
    </div>
  );
}

/* ================= NO RESPONSE TAB ================= */
export default function NoResponseTab() {
  const interviews = [
    {
      name: "David Smith",
      position: "Frontend Developer",
      date: "Feb 3, 2026",
      day: "Wednesday",
      time: "11:00 AM - 12:00 PM",
      type: "Video Call",
      noShowReason: "Interviewer did not join the interview",
    },
    {
      name: "Linda Brown",
      position: "UI/UX Designer",
      date: "Feb 3, 2026",
      day: "Wednesday",
      time: "1:00 PM - 2:00 PM",
      type: "Video Call",
      noShowReason: "Candidate did not attend the interview",
    },
    {
      name: "James Wilson",
      position: "Backend Developer",
      date: "Feb 4, 2026",
      day: "Thursday",
      time: "2:30 PM - 3:30 PM",
      type: "Video Call",
      noShowReason: "Candidate did not attend the interview",
    },
    {
      name: "Ahmed Raza",
      position: "DevOps Engineer",
      date: "Feb 5, 2026",
      day: "Friday",
      time: "10:00 AM - 11:00 AM",
      type: "Video Call",
      noShowReason: "Interviewer did not join the interview",
    },
  ];

  const itemsPerPage = 2;
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedItem, setSelectedItem] = useState(null);

  const totalPages = Math.ceil(interviews.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentItems = interviews.slice(startIndex, startIndex + itemsPerPage);

  const goToPage = (page) => {
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    setCurrentPage(page);
  };

  return (
    <div className="flex flex-col gap-5">
      {currentItems.map((item, index) => (
        <InterviewCard
          key={index}
          item={item}
          onView={(data) => setSelectedItem(data)}
        />
      ))}

      {/* ===== PAGINATION ===== */}
      <div className="flex justify-end items-center gap-1 text-gray-300 text-xs sm:text-sm mt-4">
        <button
          onClick={() => goToPage(1)}
          className="px-1 py-0.5 hover:bg-[#05DC7F]/20 rounded"
        >
          «
        </button>
        <button
          onClick={() => goToPage(currentPage - 1)}
          className="px-1 py-0.5 hover:bg-[#05DC7F]/20 rounded"
        >
          ‹
        </button>
        <span className="px-1 sm:px-2 py-0.5">
          Page {currentPage} of {totalPages}
        </span>
        <button
          onClick={() => goToPage(currentPage + 1)}
          className="px-1 py-0.5 hover:bg-[#05DC7F]/20 rounded"
        >
          ›
        </button>
        <button
          onClick={() => goToPage(totalPages)}
          className="px-1 py-0.5 hover:bg-[#05DC7F]/20 rounded"
        >
          »
        </button>
      </div>

      {/* ===== MODAL ===== */}
      {selectedItem && (
        <DetailsModal
          data={selectedItem}
          onClose={() => setSelectedItem(null)}
        />
      )}
    </div>
  );
}
