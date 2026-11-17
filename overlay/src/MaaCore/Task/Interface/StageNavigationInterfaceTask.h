#pragma once

#include <memory>

#include "Task/InterfaceTask.h"

namespace asst
{
class ProcessTask;
class StageNavigationTask;

class StageNavigationInterfaceTask final : public InterfaceTask
{
public:
    inline static constexpr std::string_view TaskType = "StageNavigation";

    StageNavigationInterfaceTask(const AsstCallback& callback, Assistant* inst);
    virtual ~StageNavigationInterfaceTask() override = default;

    virtual bool set_params(const json::value& params) override;

private:
    std::shared_ptr<ProcessTask> m_return_to_map_task_ptr = nullptr;
    std::shared_ptr<StageNavigationTask> m_navigation_task_ptr = nullptr;
};
}
