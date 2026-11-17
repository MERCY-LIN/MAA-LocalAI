#pragma once

#include <filesystem>
#include <string_view>

#include "Task/AbstractTaskPlugin.h"

namespace asst
{
class CopilotSettlementCaptureTaskPlugin final : public AbstractTaskPlugin
{
public:
    using AbstractTaskPlugin::AbstractTaskPlugin;
    virtual ~CopilotSettlementCaptureTaskPlugin() override = default;

    virtual bool verify(AsstMsg msg, const json::value& details) const override;
    void set_output_path(std::filesystem::path path);

protected:
    virtual bool _run() override;

private:
    static bool is_settlement_star_task(std::string_view task);

    std::filesystem::path m_output_path;
};
}
