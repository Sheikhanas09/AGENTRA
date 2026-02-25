"use client";

import { useState } from "react";
import { FaFileAlt, FaUserCheck } from "react-icons/fa";

export default function AllCandidatesTab({ onShortlist }) {
  const [candidates, setCandidates] = useState([
    {
      id: 1,
      name: "Alice Johnson",
      position: "Software Engineer",
      appliedOn: "2026-02-01",
      cvUrl: "#",
    },
    {
      id: 2,
      name: "Bob Smith",
      position: "UI/UX Designer",
      appliedOn: "2026-01-30",
      cvUrl: "#",
    },
    {
      id: 3,
      name: "Charlie Brown",
      position: "Data Analyst",
      appliedOn: "2026-01-28",
      cvUrl: "#",
    },
    {
      id: 4,
      name: "Diana Prince",
      position: "Product Manager",
      appliedOn: "2026-01-26",
      cvUrl: "#",
    },
    {
      id: 5,
      name: "Eve Adams",
      position: "QA Engineer",
      appliedOn: "2026-01-24",
      cvUrl: "#",
    },
    {
      id: 6,
      name: "Frank Wright",
      position: "Backend Developer",
      appliedOn: "2026-01-22",
      cvUrl: "#",
    },
  ]);

  const itemsPerPage = 3;
  const [currentPage, setCurrentPage] = useState(1);

  const totalPages = Math.ceil(candidates.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentCandidates = candidates.slice(
    startIndex,
    startIndex + itemsPerPage,
  );

  const goToPage = (page) => {
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    setCurrentPage(page);
  };

  const handleShortlist = (candidate) => {
    setCandidates((prev) => prev.filter((item) => item.id !== candidate.id));
    if (onShortlist) onShortlist(candidate);
  };

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <h3 className="text-white text-xl sm:text-2xl font-bold">
        All Candidates
      </h3>

      {/* ===== Table Header ===== */}
      <div className="hidden md:grid grid-cols-12 gap-4 px-2 sm:px-4 py-2 border-b border-gray-700 text-gray-400 font-semibold text-sm sm:text-base">
        <div className="col-span-1">#</div>
        <div className="col-span-3">Candidate Name</div>
        <div className="col-span-3">Applied For</div>
        <div className="col-span-3">Applied On</div>
        <div className="col-span-2 text-right">Actions</div>
      </div>

      {/* ===== Rows ===== */}
      <div className="flex flex-col divide-y divide-gray-800">
        {currentCandidates.map((candidate, index) => (
          <div
            key={candidate.id}
            className="grid grid-cols-1 md:grid-cols-12 gap-2 sm:gap-4 items-center px-2 sm:px-4 py-3 sm:py-4 hover:bg-black/20 rounded-lg transition"
          >
            {/* Serial */}
            <div className="col-span-1 text-gray-400 text-sm sm:text-base">
              {startIndex + index + 1}
            </div>

            <div className="col-span-3 text-white font-medium text-sm sm:text-base">
              {candidate.name}
            </div>

            <div className="col-span-3 text-[#05DC7F] font-semibold text-sm sm:text-base">
              {candidate.position}
            </div>

            <div className="col-span-3 text-white text-sm sm:text-base">
              {candidate.appliedOn}
            </div>

            <div className="col-span-2 flex flex-wrap gap-2 justify-start md:justify-end">
              <button
                onClick={() => window.open(candidate.cvUrl, "_blank")}
                className="flex items-center gap-2 px-2 sm:px-3 py-1.5 sm:py-2 text-xs sm:text-sm font-semibold rounded-lg
                bg-black/40 text-white border border-gray-600
                hover:border-[#05DC7F] hover:text-[#05DC7F] transition"
              >
                <FaFileAlt />
                <span className="hidden sm:inline">View CV</span>
              </button>

              <button
                onClick={() => handleShortlist(candidate)}
                className="flex items-center gap-2 px-2 sm:px-3 py-1.5 sm:py-2 bg-[#05DC7F]
                hover:bg-[#04c56f] text-black font-semibold rounded-lg shadow-md transition text-xs sm:text-sm"
              >
                <FaUserCheck />
                <span className="hidden sm:inline">Shortlist</span>
              </button>
            </div>
          </div>
        ))}

        {candidates.length === 0 && (
          <div className="text-center text-gray-400 py-10 text-sm sm:text-base">
            No candidates left to review
          </div>
        )}
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
