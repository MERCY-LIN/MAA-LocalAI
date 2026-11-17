#include "StageNavigationInterfaceTask.h"

#include "Task/Fight/StageNavigationTask.h"
#include "Task/ProcessTask.h"
#include "Utils/Logger.hpp"

asst::StageNavigationInterfaceTask::StageNavigationInterfaceTask(const AsstCallback& callback, Assistant* inst) :
    InterfaceTask(callback, inst, TaskType),
    m_return_to_map_task_ptr(std::make_shared<ProcessTask>(callback, inst, TaskType)),
    m_navigation_task_ptr(std::make_shared<StageNavigationTask>(callback, inst, TaskType))
{
    LogTraceFunction;
    m_return_to_map_task_ptr->set_tasks({ "Return" }).set_retry_times(1).set_ignore_error(true);
}

bool asst::StageNavigationInterfaceTask::set_params(const json::value& params)
{
    const std::string stage = params.get("stage", std::string());
    const bool return_to_map = params.get("return_to_map", false);
    if (stage.empty()) {
        LogError << __FUNCTION__ << "| navigation stage is empty";
        return false;
    }
    if (!m_navigation_task_ptr->set_stage_name(stage)) {
        LogError << __FUNCTION__ << "| unsupported navigation stage" << stage;
        return false;
    }

    m_subtasks.clear();
    if (return_to_map) {
        m_subtasks.emplace_back(m_return_to_map_task_ptr);
    }
    m_subtasks.emplace_back(m_navigation_task_ptr);
    return true;
}
