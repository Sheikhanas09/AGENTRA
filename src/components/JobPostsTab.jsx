"use client";
import { useState } from "react";
import { FaEye } from "react-icons/fa";

export default function JobPostsTab({ setSelectedJob }) {
  const jobPosts = [
    {
      id: 1,
      title: "Frontend Developer",
      postedAt: "2026-02-01 10:30 AM",
      description: `
Bitzsol Digital is on the lookout for WordPress & Shopify Development Interns who are ready to learn, build, and grow.

If you’re a final-semester student or a fresh grad who actually wants hands-on experience, keep reading:

💻 Open Roles
✨ WordPress Development Intern
✨ Shopify Development Intern

What’s In It For You?
• Real projects.
• Mentorship.
• Skill-building + career growth
• Internship that can turn into a full-time role 💼

📍 Job Details
• Type: On-site
• Location: Saidpur Road, Rawalpindi

📩 Drop your CV at:
hr@bitzsol.com
      `,
    },
    {
      id: 2,
      title: "Backend Developer",
      postedAt: "2026-01-29 02:15 PM",
      description: `- Build scalable APIs using Node.js and Express...`,
    },
    {
      id: 3,
      title: "UI/UX Designer",
      postedAt: "2026-01-25 11:00 AM",
      description: `- Design modern UI/UX for web and mobile applications...`,
    },
  ];

  // ===== Pagination state =====
  const itemsPerPage = 2; // You can adjust per page
  const [currentPage, setCurrentPage] = useState(1);
  const totalPages = Math.ceil(jobPosts.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentJobs = jobPosts.slice(startIndex, startIndex + itemsPerPage);

  const goToPage = (page) => {
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    setCurrentPage(page);
  };

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <h3 className="text-white text-xl sm:text-2xl font-bold">Job Posts</h3>

      {/* Table Header */}
      <div className="hidden md:grid grid-cols-12 gap-4 px-2 sm:px-4 py-2 border-b border-gray-700 text-gray-400 font-semibold text-sm sm:text-base">
        <div className="col-span-1">#</div>
        <div className="col-span-5">Job Title</div>
        <div className="col-span-4">Posted On</div>
        <div className="col-span-2 text-right">Action</div>
      </div>

      {/* Rows */}
      <div className="flex flex-col divide-y divide-gray-800">
        {currentJobs.map((job, index) => (
          <div
            key={job.id}
            className="grid grid-cols-1 md:grid-cols-12 gap-2 sm:gap-4 items-center px-2 sm:px-4 py-3 sm:py-4 hover:bg-black/20 rounded-lg transition"
          >
            <div className="col-span-1 text-gray-400 text-sm sm:text-base">
              {startIndex + index + 1}
            </div>
            <div className="col-span-5 text-white font-medium text-sm sm:text-base">
              {job.title}
            </div>
            <div className="col-span-4 text-white text-sm sm:text-base">
              {job.postedAt}
            </div>
            <div className="col-span-2 flex flex-wrap justify-start md:justify-end gap-2">
              <button
                onClick={() => setSelectedJob(job)}
                className="flex items-center gap-2 px-2 sm:px-4 py-1.5 sm:py-2 text-xs sm:text-sm font-semibold rounded-lg
                  bg-black/40 text-white border border-gray-600
                  hover:border-[#05DC7F] hover:text-[#05DC7F] transition"
              >
                <FaEye />
                <span className="hidden sm:inline">View</span>
              </button>
            </div>
          </div>
        ))}

        {jobPosts.length === 0 && (
          <div className="text-center text-gray-400 py-10 text-sm sm:text-base">
            No job posts available
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
