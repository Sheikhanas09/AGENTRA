"use client";

import { useState } from "react";
import { FaFileAlt, FaCalendarCheck } from "react-icons/fa";

export default function ShortlistedTab() {
  const shortlistedCandidates = [
    {
      name: "Alice Johnson",
      position: "Software Engineer",
      date: "2026-02-01",
    },
    { name: "Bob Smith", position: "UI/UX Designer", date: "2026-01-28" },
    { name: "Charlie Brown", position: "Data Analyst", date: "2026-01-25" },
    { name: "Diana Prince", position: "Product Manager", date: "2026-01-20" },
    { name: "Eve Adams", position: "QA Engineer", date: "2026-01-18" },
    { name: "Frank Wright", position: "Backend Developer", date: "2026-01-15" },
    { name: "Grace Lee", position: "Frontend Developer", date: "2026-01-12" },
    { name: "Harry Potter", position: "Intern", date: "2026-01-10" },
  ];

  const itemsPerPage = 3;
  const [currentPage, setCurrentPage] = useState(1);

  const totalPages = Math.ceil(shortlistedCandidates.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;

  const currentCandidates = shortlistedCandidates.slice(
    startIndex,
    startIndex + itemsPerPage,
  );

  const goToPage = (page) => {
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    setCurrentPage(page);
  };

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <h3 className="text-white text-xl sm:text-2xl font-bold mb-2 sm:mb-4">
        Shortlisted Candidates
      </h3>

      {/* ===== Table Header (hidden on mobile) ===== */}
      <div className="hidden md:grid grid-cols-12 gap-4 text-gray-400 font-semibold px-2 sm:px-4 py-2 border-b border-gray-700 text-sm sm:text-base">
        <div className="col-span-1">#</div>
        <div className="col-span-3">Candidate Name</div>
        <div className="col-span-3">Position</div>
        <div className="col-span-3">Shortlisted On</div>
        <div className="col-span-2 text-right">Actions</div>
      </div>

      {/* ===== Candidates Rows ===== */}
      <div className="flex flex-col divide-y divide-gray-700">
        {currentCandidates.map((candidate, index) => (
          <div
            key={index}
            className="grid grid-cols-1 md:grid-cols-12 gap-2 sm:gap-4 items-center py-3 sm:py-4 px-2 sm:px-4 hover:bg-black/20 rounded-lg transition"
          >
            {/* Serial No */}
            <div className="col-span-1 text-gray-400 text-sm sm:text-base">
              {startIndex + index + 1}
            </div>

            {/* Candidate Name */}
            <div className="col-span-3 text-white font-medium text-sm sm:text-base">
              {candidate.name}
            </div>

            {/* Position */}
            <div className="col-span-3 text-[#05DC7F] font-semibold text-sm sm:text-base">
              {candidate.position}
            </div>

            {/* Shortlisted On */}
            <div className="col-span-3 text-white font-medium text-sm sm:text-base">
              {candidate.date}
            </div>

            {/* Actions */}
            <div className="col-span-2 flex flex-wrap gap-2 justify-start md:justify-end">
              <button
                className="flex items-center gap-2 px-2 sm:px-3 py-1.5 sm:py-2 text-xs sm:text-sm font-semibold rounded-lg
                bg-black/40 text-white border border-gray-600
                hover:border-[#05DC7F] hover:text-[#05DC7F] transition"
              >
                <FaFileAlt />
                <span className="hidden sm:inline">View CV</span>
              </button>

              <button
                className="flex items-center gap-2 px-2 sm:px-3 py-1.5 sm:py-2 bg-[#05DC7F]
                hover:bg-[#04c56f] text-black font-semibold rounded-lg shadow-md transition text-xs sm:text-sm"
              >
                <FaCalendarCheck />
                <span className="hidden sm:inline">Schedule</span>
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* ===== Pagination ===== */}
      <div className="flex flex-wrap justify-end items-center gap-2 mt-2 sm:mt-4 text-gray-300 text-sm sm:text-base">
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

        <span className="px-2 sm:px-3 py-1">
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
  );
}
