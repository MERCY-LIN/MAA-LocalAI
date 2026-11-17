#include "CopilotSettlementCaptureTaskPlugin.h"

#include <system_error>

#include "MaaUtils/ImageIo.h"
#include "Utils/Logger.hpp"
#include "Utils/Platform.hpp"

bool asst::CopilotSettlementCaptureTaskPlugin::is_settlement_star_task(std::string_view task)
{
    return task.ends_with("StageDrops-Stars-3") || task.ends_with("StageDrops-Stars-Adverse");
}

void asst::CopilotSettlementCaptureTaskPlugin::set_output_path(std::filesystem::path path)
{
    m_output_path = path;
}

bool asst::CopilotSettlementCaptureTaskPlugin::verify(AsstMsg msg, const json::value& details) const
{
    if (m_output_path.empty() || msg != AsstMsg::SubTaskStart ||
        details.get("subtask", std::string()) != "ProcessTask") {
        return false;
    }
    return is_settlement_star_task(details.get("details", "task", std::string()));
}

bool asst::CopilotSettlementCaptureTaskPlugin::_run()
{
    auto image = get_hit_image();
    if (!image || image->empty()) {
        LogError << __FUNCTION__ << "| settlement hit image is unavailable";
        return false;
    }

    std::error_code ec;
    if (!m_output_path.parent_path().empty()) {
        std::filesystem::create_directories(m_output_path.parent_path(), ec);
        if (ec) {
            LogError << __FUNCTION__ << "| cannot create settlement directory" << m_output_path.parent_path()
                     << ec.message();
            return false;
        }
    }
    if (!MAA_NS::imwrite(m_output_path, *image)) {
        LogError << __FUNCTION__ << "| cannot save settlement image" << m_output_path;
        return false;
    }

    json::value info = basic_info_with_what("CopilotSettlementCaptured");
    info["details"]["path"] = m_output_path.string();
    callback(AsstMsg::SubTaskExtraInfo, info);
    return true;
}
